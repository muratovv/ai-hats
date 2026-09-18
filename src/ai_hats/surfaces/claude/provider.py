"""Claude Code surface plugin for ai-hats."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from .. import SurfaceHint
    from ai_hats_observe.canonical.reader import EventReader
    from ai_hats_observe.event_log_writer import EventSource
    from ai_hats_observe.parsers.base import TranscriptParser

from ai_hats_core import CompositionResult
from ai_hats_observe.parsers.claude import ClaudeParser
from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader
from .. import (
    MetricsSink,
    Surface,
    SubagentEngine,
    SurfaceRunResult,
)
from ai_hats.materialization import describe_mkdir, describe_write_text
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ..plan import CompositionPlan, Host, Launch, Launched, LaunchFlags, MaterializationPlan
from .sdk_options import describe_options, render_sdk_audit
from . import sdk_runner
from .channel import DISPATCHER_COMMAND, DISPATCHER_TAG, HOOK_NOTIFICATION, OBSERVED_NOTIFICATION
from .runtime_hooks import plan_hooks

from ai_hats.paths import (
    AI_HATS_PROJECT_DIR_ENV,
    ENV_AI_HATS_DIR,
    claude_plugin_skills_dir,
    claude_settings_json,
    claude_settings_local_json,
    claude_user_settings_json,
)
from ai_hats.constants import (
    INJECTION_START,
    INJECTION_END,
    PROVIDER_CLAUDE,
)

logger = logging.getLogger(__name__)


def _entry_matcher(rows: list[dict[str, str]]) -> str:
    """One matcher covering every row's, in the alternation the entries already
    shipped (`Bash|run_command|execute`).

    `*` would be one character and costs ~45 ms on every call no gate wants —
    a dispatcher spawned to find that nothing matched, where the harness spawns
    nothing at all (measured on `Read`).
    """
    alternatives = [name for row in rows for name in row["matcher"].split("|")]
    if any(name in ("", "*") for name in alternatives):
        return "*"
    return "|".join(dict.fromkeys(alternatives))


@dataclass(frozen=True)
class SettingsFinding:
    """One deprecated permission rule: where it is and what replaces it."""

    source: Path
    array: str
    rule: str
    replacement: str


# ----- Claude settings lint (docs/session-start-notices.md) -----

# Claude Code >=2.1.210: file-permission checks match only Edit()/Read() rules.
DEPRECATED_RULE_TOOLS: tuple[tuple[str, str], ...] = (
    ("Write", "Edit"),
    ("NotebookEdit", "Edit"),
    ("Glob", "Read"),
)

_PERMISSION_ARRAYS = ("allow", "deny", "ask")


def lint_permission_rules(settings: object, *, source: Path) -> list[SettingsFinding]:
    """Findings for every deprecated permission rule in one parsed settings doc.

    Tolerates any malformed shape (non-dict nodes, non-string rules) by
    skipping it — the caller's fail-open contract, applied at field level.
    """
    if not isinstance(settings, dict):
        return []
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        return []
    findings: list[SettingsFinding] = []
    for array in _PERMISSION_ARRAYS:
        rules = permissions.get(array)
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, str):
                continue
            for tool, replacement_tool in DEPRECATED_RULE_TOOLS:
                prefix = f"{tool}("
                if rule.startswith(prefix):
                    replacement = f"{replacement_tool}({rule[len(prefix) :]}"
                    findings.append(SettingsFinding(source, array, rule, replacement))
                    break
    return findings


def lint_settings_files(paths: "Iterable[Path]") -> list[SettingsFinding]:
    """Findings across a settings-file chain; per-file fail-open.

    A missing, unreadable, or non-JSON file contributes nothing — a broken
    settings file is Claude Code's own loud failure, not this lint's.
    """
    findings: list[SettingsFinding] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        findings.extend(lint_permission_rules(data, source=path))
    return findings


class ClaudeSurface(Surface):
    @property
    def name(self) -> str:
        return PROVIDER_CLAUDE

    def supports_session_command_wrappers(self) -> bool:
        return True

    def surface_hints(self) -> list["SurfaceHint"]:
        from .. import SurfaceHint

        return [
            SurfaceHint(
                name="--model",
                values="claude-3-5-sonnet-20241022, ...",
                description="Overrides the model to use for the session.",
            ),
        ]

    def transcript_parser(self) -> TranscriptParser:
        # Claude emits a structured JSONL session log → richer parse.
        return ClaudeParser()

    def event_reader(self) -> Callable[[Path], EventReader]:
        # One reader per transcript path, holding its own position; live, so the
        # tail is held open until the session's writer says the run is over.
        return partial(ClaudeTranscriptReader, live=True)

    def resolve_transcript(
        self,
        cwd: Path,
        session_id: str,
        *,
        provider_session_id: str | None = None,
        end_ts: float | None = None,
    ) -> list[Path]:
        from ai_hats.paths import claude_transcript_path, claude_transcripts_dir, resolve_transcript

        return resolve_transcript(
            claude_transcripts_dir(cwd),
            "*.jsonl",
            session_id,
            exact_path=claude_transcript_path(cwd, provider_session_id)
            if provider_session_id
            else None,
            end_ts=end_ts,
        )

    def event_sources(
        self,
        cwd: Path,
        session_id: str,
        *,
        provider_session_id: str | None = None,
    ) -> "list[EventSource]":
        # A sub-agent's record sits under <sid>/subagents/, named by its id; it
        # is followed only beside a main record, never as a run of its own.
        from ai_hats.paths import claude_subagent_transcripts
        from ai_hats_observe.canonical import AgentId
        from ai_hats_observe.event_log_writer import EventSource

        sources = super().event_sources(cwd, session_id, provider_session_id=provider_session_id)
        if not sources or not provider_session_id:
            return sources
        sources.extend(
            EventSource(path, agent=AgentId(agent_id))
            for agent_id, path in claude_subagent_transcripts(cwd, provider_session_id)
        )
        return sources

    def system_prompt_path(self, layout: ProjectLayout) -> Path | None:
        """Claude uses per-session prompt cache; no root CLAUDE.md managed."""
        del layout
        return None

    def update_system_prompt(self, layout: ProjectLayout, content: str) -> Path | None:
        """Claude uses session-cache prompt, root CLAUDE.md is untouched."""
        del layout, content
        return None

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        # Skills reach the agent via the native --plugin-dir (HITL) / SDK
        # plugin (sub-agent) registry the plan writes, so the sections carry
        # no skill index — it would be a 2-3x
        # duplicate listing (~1.5k tok/session).
        return self._compose_sections(result)

    # SETTINGS delivers nothing in either mode — hence no handler for it.

    # -- the plan (ADR-0036 D2): entries, env and launch from the composition half --

    def plan(
        self,
        composition: CompositionPlan,
        *,
        run_mode: RunMode,
        policy: SessionPolicy,
        root: Path,
        layout: ProjectLayout,
        host: Host,
    ) -> MaterializationPlan:
        mode = RunMode(run_mode)
        hitl = mode is RunMode.HITL
        entries = [describe_mkdir(root)]
        args: list[str] = []
        options: dict[str, object] = {}
        env: dict[str, str] = {}
        prompt = composition.prompt  # claude adds no block of its own
        context: Path | None = None
        if policy.context:
            context = root / "prompt.md"
            entries.append(
                describe_write_text(context, self._build_full_content(layout, prompt.text))
            )
            if hitl:
                args += ["--system-prompt-file", str(context)]
            else:
                options["system_prompt"] = {
                    "type": "preset",
                    "preset": "claude_code",
                    "append": prompt.text,
                }
        from .plugin_dir import path_dirs, plan_plugin

        plugin_dir = root / "plugin"
        entries += plan_plugin(composition, plugin_dir)
        if dirs := path_dirs(composition, plugin_dir):
            env["PATH"] = os.pathsep.join([*(str(d) for d in dirs), host.path])
        if hitl:
            args += ["--plugin-dir", str(plugin_dir)]
        else:
            options["plugins"] = (
                [{"type": "local", "path": str(plugin_dir)}] if composition.skills else []
            )
        if policy.hooks:
            manifest, rows, hook_env = plan_hooks(composition, root, host)
            settings = root / "settings.json"
            entries.append(manifest)
            entries.append(
                describe_write_text(
                    settings,
                    json.dumps(
                        {self._SETTINGS_HOOKS_KEY: self._desired_runtime_entries(rows)}, indent=2
                    ),
                )
            )
            env.update(hook_env)
            if hitl:
                args += ["--settings", str(settings)]
            else:
                options["settings"] = str(settings)
                options["setting_sources"] = []
        env.update(self.get_env(root, layout))
        return MaterializationPlan(
            composition=composition,
            prompt=prompt,
            surface=self.name,
            run_mode=mode,
            policy=policy,
            root=root,
            entries=tuple(entries),
            env=env,
            launch=Launch(args=tuple(args), sdk_options=None)
            if hitl
            else Launch(args=None, sdk_options=options),
            context=context,
        )

    def automate_launch(
        self,
        plan: MaterializationPlan,
        flags: LaunchFlags,
        env: Mapping[str, str],
        *,
        layout: ProjectLayout,
    ) -> Launched:
        """No argv here — the launch IS the option document handed to the SDK:
        the plan's options, then what only this run knows."""
        options: dict[str, object] = dict(plan.launch.sdk_options or {})
        options["cwd"] = str(flags.work_dir if flags.work_dir is not None else layout.root)
        if flags.provider_session_id is not None:
            options["session_id"] = flags.provider_session_id
        if flags.model:
            options["model"] = flags.model
        if env:
            options["env"] = dict(env)
        return Launched(
            args=None,
            sdk_options=options,
            env=env,
            prompt=render_sdk_audit(plan.prompt.text, flags.brief or ""),
        )

    def describe_launch(self, launched: Launched) -> list[str]:
        if launched.sdk_options is None:
            return list(launched.args or ())
        from claude_agent_sdk import ClaudeAgentOptions

        return describe_options(ClaudeAgentOptions(**launched.sdk_options))

    def _plugin_dir(self, layout: ProjectLayout, session_id: str) -> Path:
        return layout.cache.session(session_id) / "plugin"

    def session_skills_root(self, layout: ProjectLayout, session_id: str) -> Path:
        """Writer and reader share this, so the two cannot drift."""
        return claude_plugin_skills_dir(self._plugin_dir(layout, session_id))

    def engine(self) -> "SubagentEngine | None":
        return ClaudeSubagentEngine(self)

    def _build_full_content(self, layout: ProjectLayout, prompt_content: str) -> str:
        """Build prompt content without splicing root CLAUDE.md."""
        return f"{INJECTION_START}\n{prompt_content}\n{INJECTION_END}\n"

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["claude"]
        if args:
            cmd.extend(args)
        return cmd

    def get_cli_launch_args(
        self, base_cmd: list[str], session_id: str, is_resume: bool
    ) -> list[str]:
        if not is_resume:
            return base_cmd + ["--session-id", session_id]
        return base_cmd

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
        *,
        model: str | None = None,
    ) -> list[str]:
        extra = ["--model", model] if model else []
        return cmd + extra + ["--print", "-p", meta_prompt]

    def serve_hooks(self, layout: ProjectLayout, session_id: str, environ: dict[str, str]):
        """One warm dispatcher for the session instead of one per tool call.

        Measured on a composed maintainer session: 125 ms per gated call spawned,
        94 ms asked — against 72 ms for the gates alone.
        """
        from .hook_server import HookServer

        return HookServer(layout.cache.session(session_id), dict(environ)).start()

    def get_env(self, session_dir: Path, layout: ProjectLayout) -> dict[str, str]:
        project_dir = layout.root
        # Hand every runtime hook a clean writable anchor so it need
        # not derive WRITE paths from ``__file__`` depth — materialization
        # relocates the script, so a ``__file__``-relative write can land in a
        # source tree (the secret-guard ``.log`` incident). Inherited by hook
        # subprocesses via the launched provider env (``wrap_runner``). Honours
        # an ambient ``AI_HATS_DIR`` override (precedence lives in ``ai_hats_dir``).
        # Pair var scopes the pin to THIS project — the resolver
        # drops a leaked foreign pair, so get_env re-pins fresh values here.
        return {
            ENV_AI_HATS_DIR: str(layout.base),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
        }

    # ----- PreToolUse hook wiring -----

    # settings.json root key holding the hooks map (also the per-entry command list).
    _SETTINGS_HOOKS_KEY = "hooks"
    _LEAKED_PROJECT_HOOK_MARKERS = ("plugin/skills/", "ai-hats/library/hooks/")

    def _desired_runtime_entries(
        self, rows: dict[str, list[dict[str, str]]]
    ) -> dict[str, list[dict]]:
        """``{event: [the dispatcher entry]}`` for the rows the manifest holds,
        plus the one observer that rides no skill.

        One entry per event: WHICH gates a call matched is the dispatcher's to
        answer from the manifest, and the harness only has to deliver the call.
        An event the composition binds nothing to gets no entry — except the
        notification that claude is showing the person its permission prompt,
        which no gate judges and only a hook can see: the dispatcher records
        it into the session's own log, and runs only then.
        """
        entries = {
            event: [self._dispatcher_entry(event, _entry_matcher(event_rows))]
            for event, event_rows in rows.items()
        }
        entries[HOOK_NOTIFICATION] = [
            self._dispatcher_entry(HOOK_NOTIFICATION, OBSERVED_NOTIFICATION)
        ]
        return entries

    def _dispatcher_entry(self, event: str, matcher: str) -> dict:
        return {
            "matcher": matcher,
            "_ai_hats_managed": f"{DISPATCHER_TAG}:{event}",
            self._SETTINGS_HOOKS_KEY: [{"type": "command", "command": DISPATCHER_COMMAND}],
        }

    def leaked_user_global_project_hooks(self, home: "Path") -> list[str]:
        """ai-hats project-hook commands leaked into ``<home>/.claude/settings.json``.

        Any ai-hats hook in user-global settings is a leak (double-fires + 404s
        off project-root). Matched by command substring — not the
        ``_ai_hats_managed`` tag — so a half-migrated mix of tagged/untagged
        entries is caught. Pure: returns the commands (empty when absent /
        unreadable / clean), never prints or mutates.
        """
        settings = claude_settings_json(home)
        try:
            raw = settings.read_text()
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, ValueError):
            return []  # missing / unreadable / non-UTF8 / malformed — never crash
        if not isinstance(data, dict):
            return []
        hooks_root = data.get(self._SETTINGS_HOOKS_KEY)
        if not isinstance(hooks_root, dict):
            return []

        leaked: list[str] = []
        for event_list in hooks_root.values():
            for entry in event_list if isinstance(event_list, list) else []:
                if not isinstance(entry, dict):
                    continue
                for hook in entry.get(self._SETTINGS_HOOKS_KEY, []) or []:
                    if not isinstance(hook, dict):
                        continue
                    command = str(hook.get("command", ""))
                    if any(m in command for m in self._LEAKED_PROJECT_HOOK_MARKERS):
                        leaked.append(command)
        return leaked

    def settings_lint_warnings(self, layout: ProjectLayout) -> list[str]:
        """One warning per deprecated permission rule in the Claude settings
        chain (user-global + project + local). Warn-only — the settings files
        are user-owned and never mutated."""
        project_dir = layout.root
        findings = lint_settings_files(
            [
                claude_user_settings_json(),
                claude_settings_json(project_dir),
                claude_settings_local_json(project_dir),
            ]
        )
        return [
            f"{f.source}: {f.array} rule {f.rule} is ignored by Claude Code "
            f"≥2.1.210 — replace with {f.replacement}"
            for f in findings
        ]


def _stream_signals_to(event_log: Path | None) -> "Callable[[object], None] | None":
    """The seam's listener: what only the stream carries — a rate-limit
    message — appended to the session's log, stamped as it arrives. The
    transcript owns everything else, so nothing else is written twice.
    Fail-open: a line that cannot land is logged, the run goes on."""
    if event_log is None:
        return None
    from ai_hats_observe.canonical import now
    from ai_hats_observe.event_log import write_events

    from .stream_events import rate_limit_events

    def listen(message: object) -> None:
        info = getattr(message, "rate_limit_info", None)
        if info is None or type(message).__name__ != "RateLimitEvent":
            return
        try:
            write_events(rate_limit_events(info, ts=now()), event_log, append=True)
        except Exception:
            logger.warning("rate-limit signal not recorded in %s", event_log, exc_info=True)

    return listen


class ClaudeSubagentEngine(SubagentEngine):
    def __init__(self, provider: ClaudeSurface, *, run_blocking: Callable | None = None) -> None:
        self._provider = provider
        # The SDK call is a seam, not an import three frames down: a test drives the
        # engine by handing in its own, instead of patching the module under test.
        self._run_blocking = run_blocking or sdk_runner.run_claude_sdk_blocking

    def run(
        self,
        *,
        layout: ProjectLayout,
        work_dir: Path,
        session_id: str,
        env: dict[str, str],
        model: str | None,
        timeout_s: int,
        metrics: MetricsSink,
        launched: Launched,
        brief: str | None,
        provider_session_id: str | None = None,
        event_log: Path | None = None,
    ) -> SurfaceRunResult:
        """The option document is the launch: ``automate_launch`` already folded
        the working directory, the model, the env and the minted id into it."""
        from claude_agent_sdk import ClaudeAgentOptions

        del layout, work_dir, session_id, env, model, provider_session_id
        opts = ClaudeAgentOptions(**(launched.sdk_options or {}))
        run_res = self._run_blocking(
            opts, brief or "", timeout_s=timeout_s, on_message=_stream_signals_to(event_log)
        )

        metrics.record(
            {
                "claude_session_id": run_res.claude_session_id,
                "total_cost_usd": run_res.total_cost_usd,
                "num_turns": run_res.num_turns,
                "stop_reason": run_res.stop_reason,
            }
        )
        return SurfaceRunResult(
            exit_code=run_res.exit_code,
            stdout=run_res.stdout,
            stderr=run_res.stderr,
            timed_out=run_res.timed_out,
            error=run_res.error,
        )
