"""Claude Code surface plugin for ai-hats."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from ai_hats.providers import ProviderHint
    from ai_hats_observe.parsers.base import TranscriptParser

from ai_hats_core import CompositionResult
from ai_hats_observe.parsers.claude import ClaudeParser
from ai_hats.providers import Provider, ProviderRunResult, SubagentEngine
from ai_hats.session_artifacts import ArtifactCategory, BuiltArtifacts, RunMode
from .sdk_options import build_first_user_message, build_options
from . import sdk_runner

from ai_hats.hook_collection import collect_runtime_hooks, resolve_skill_script
from ai_hats.skills_dir import inject_skill_paths_to_env
from ai_hats.paths import (

    AI_HATS_PROJECT_DIR_ENV,
    ENV_AI_HATS_DIR,
    CLAUDE_PROJECT_DIR_VAR,
    ai_hats_dir,
    claude_md,
    claude_settings_json,
    claude_settings_local_json,
    claude_user_settings_json,
    hooks_dir as _lib_hooks_dir,
    managed_runtime_hook_filename,
    session_cache_dir,
)
from ai_hats.placeholders import expand_path_placeholders
from ai_hats.role_catalog import expand_role_catalog
from ai_hats.constants import (
    HOOK_PRE_TOOL_USE,
    INJECTION_START,
    INJECTION_END,
    PROVIDER_CLAUDE,
)


@dataclass(frozen=True)
class SettingsFinding:
    """One deprecated permission rule: where it is and what replaces it."""

    source: Path
    array: str
    rule: str
    replacement: str


# ----- HATS-1006 Claude settings lint (docs/session-start-notices.md) -----

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

class ClaudeProvider(Provider):
    @property
    def name(self) -> str:
        return PROVIDER_CLAUDE

    def provider_hints(self) -> list["ProviderHint"]:
        from ai_hats.providers import ProviderHint
        return [
            ProviderHint(
                name="--model",
                values="claude-3-5-sonnet-20241022, ...",
                description="Overrides the model to use for the session.",
            ),
        ]

    def transcript_parser(self) -> TranscriptParser:
        # HATS-948: Claude emits a structured JSONL session log → richer parse.
        return ClaudeParser()

    def resolve_transcript(
        self, project_dir: Path, session_id: str, *, provider_session_id: str | None = None
    ) -> Path | None:
        from ai_hats.paths import claude_transcript_path, claude_transcripts_dir, resolve_transcript

        return resolve_transcript(
            claude_transcripts_dir(project_dir), "*.jsonl", session_id,
            exact_path=claude_transcript_path(project_dir, provider_session_id)
            if provider_session_id else None,
        )

    def system_prompt_path(self, project_dir: Path) -> Path:
        return claude_md(project_dir)

    def update_system_prompt(self, project_dir: Path, content: str) -> None:
        """HATS-1170: Claude uses session-cache prompt, root CLAUDE.md is untouched."""
        pass

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        # HATS-701: skills reach the agent via the native --plugin-dir (HITL)
        # / SDK plugin (sub-agent) registry materialized in build_session_prompt
        # / sdk_options. Suppress the AVAILABLE SKILLS index here to avoid the
        # 2-3x duplicate listing (~1.5k tok/session).
        return self._compose_sections(result, include_skills=False)

    def build_category_artifact(
        self,
        category: ArtifactCategory,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
        *,
        run_mode: RunMode,
        artifacts: BuiltArtifacts,
    ) -> None:
        """Materialize a single category of session artifacts for ClaudeProvider (ADR-0018)."""
        cache_dir = session_cache_dir(project_dir, session_id)
        artifacts.port.mkdir(cache_dir)

        if category == ArtifactCategory.CONTEXT:
            self._build_context_artifact(project_dir, result, cache_dir, run_mode, artifacts)
        elif category == ArtifactCategory.SKILLS:
            self._build_skills_artifact(project_dir, result, session_id, cache_dir, run_mode, artifacts)
        elif category == ArtifactCategory.HOOKS:
            self._build_hooks_artifact(project_dir, result, cache_dir, run_mode, artifacts)
        elif category == ArtifactCategory.SETTINGS:
            self._build_settings_artifact(project_dir, result, cache_dir, run_mode, artifacts)

    def _build_context_artifact(
        self,
        project_dir: Path,
        result: CompositionResult,
        cache_dir: Path,
        mode: RunMode,
        artifacts: BuiltArtifacts,
    ) -> None:
        prompt_content = self.build_system_prompt(result)
        prompt_content = expand_path_placeholders(prompt_content, project_dir)
        prompt_content = expand_role_catalog(prompt_content, project_dir)

        full_content = self._build_full_content(project_dir, prompt_content)
        artifacts.full_content = full_content

        override_file = cache_dir / "prompt.md"
        artifacts.port.write_text(override_file, full_content)
        artifacts.materialized.append(override_file)

        if mode == RunMode.HITL:
            artifacts.cli_args.extend(["--system-prompt-file", str(override_file)])
        elif mode == RunMode.AUTOMATE:
            text = expand_path_placeholders(self.build_system_prompt(result), project_dir)
            artifacts.sdk_options["system_prompt"] = {"type": "preset", "preset": "claude_code", "append": text}

    def _build_skills_artifact(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
        cache_dir: Path,
        mode: RunMode,
        artifacts: BuiltArtifacts,
    ) -> None:
        # Not via materialize_runtime_skills: that is a published extension point
        # and cannot take the port (HATS-1211 / HATS-1207 R4).
        from .plugin_dir import materialize_plugin_dir

        plugin_dir = cache_dir / "plugin"
        materialize_plugin_dir(
            result.name, result.skills, project_dir, plugin_dir, artifacts.port
        )
        if mode == RunMode.HITL:
            artifacts.cli_args.extend(["--plugin-dir", str(plugin_dir)])
        elif mode == RunMode.AUTOMATE:
            if result.skills:
                artifacts.sdk_options["plugins"] = [{"type": "local", "path": str(plugin_dir)}]
            else:
                artifacts.sdk_options["plugins"] = []
        plugin_skills_dir = cache_dir / "plugin" / "skills"
        inject_skill_paths_to_env(artifacts.extra_env, result.skills, plugin_skills_dir)
        artifacts.materialized.append(cache_dir / "plugin")

    def _build_hooks_artifact(
        self,
        project_dir: Path,
        result: CompositionResult,
        cache_dir: Path,
        mode: RunMode,
        artifacts: BuiltArtifacts,
    ) -> None:
        desired = self._desired_runtime_entries(project_dir, result)
        cache_settings = cache_dir / "settings.json"
        artifacts.port.write_text(
            cache_settings, json.dumps({self._SETTINGS_HOOKS_KEY: desired}, indent=2)
        )
        artifacts.materialized.append(cache_settings)

        if mode == RunMode.HITL:
            artifacts.cli_args.extend(["--settings", str(cache_settings)])
        elif mode == RunMode.AUTOMATE:
            artifacts.sdk_options["settings"] = str(cache_settings)
            artifacts.sdk_options["setting_sources"] = []

    def _build_settings_artifact(
        self,
        project_dir: Path,
        result: CompositionResult,
        cache_dir: Path,
        mode: RunMode,
        artifacts: BuiltArtifacts,
    ) -> None:
        """Provider settings / permissions category. Currently dormant for ClaudeProvider."""
        pass

    def build_session_prompt(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        """Write composed prompt & session artifacts via build_session_artifacts."""
        artifacts = self.build_session_artifacts(
            project_dir, result, session_id, run_mode=RunMode.HITL,
            artifacts=BuiltArtifacts(),
        )
        return (artifacts.cli_args, artifacts.extra_env, artifacts.full_content or "")

    def supports_sdk_engine(self) -> bool:
        """Indicates this provider uses the Python SDK path."""
        return True

    def engine(self) -> "SubagentEngine | None":
        return ClaudeSubagentEngine(self)

    def _build_full_content(self, project_dir: Path, prompt_content: str) -> str:
        """Build prompt content without splicing root CLAUDE.md (HATS-704 / HATS-1170)."""
        return f"{INJECTION_START}\n{prompt_content}\n{INJECTION_END}\n"

    def materialize_runtime_skills(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> list[str]:
        """Materialize composed role's skills into a per-session plugin-dir.

        Returns ``["--plugin-dir", <cache_dir>/plugin]``. The dir lives under
        ``<ai_hats_dir>/.cache/sessions/<session_id>/plugin/`` and is cleaned
        with the whole cache dir at session_end. Empty skill list still
        produces a valid (empty) plugin-dir so the argument is always
        consistent — the no-skills case is free.
        """
        from ai_hats.materialization import ApplyMaterializer

        from .plugin_dir import materialize_plugin_dir

        # A published extension point cannot carry the port, so this path always
        # writes — it is one of the builder bypasses HATS-1207 removes.
        plugin_dir = session_cache_dir(project_dir, session_id) / "plugin"
        materialize_plugin_dir(
            result.name, result.skills, project_dir, plugin_dir, ApplyMaterializer()
        )
        return ["--plugin-dir", str(plugin_dir)]

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["claude"]
        if args:
            cmd.extend(args)
        return cmd

    def get_cli_launch_args(self, base_cmd: list[str], session_id: str, is_resume: bool) -> list[str]:
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

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        # HATS-819: hand every runtime hook a clean writable anchor so it need
        # not derive WRITE paths from ``__file__`` depth — materialization
        # relocates the script, so a ``__file__``-relative write can land in a
        # source tree (the secret-guard ``.log`` incident). Inherited by hook
        # subprocesses via the launched provider env (``wrap_runner``). Honours
        # an ambient ``AI_HATS_DIR`` override (precedence lives in ``ai_hats_dir``).
        # HATS-897: pair var scopes the pin to THIS project — the resolver
        # drops a leaked foreign pair, so get_env re-pins fresh values here.
        return {
            ENV_AI_HATS_DIR: str(ai_hats_dir(project_dir)),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
        }

    # ----- HATS-437: PreToolUse hook auto-wire -----

    # Marker tag on managed PreToolUse entries. Lets ``ensure_runtime_hooks``
    # locate prior installs and update them in place rather than appending
    # a duplicate. User-authored entries (without the tag) are never touched.
    _MANAGED_HOOK_TAG = "ai-hats:hats-437"

    # settings.json root key holding the hooks map (also the per-entry command list).
    _SETTINGS_HOOKS_KEY = "hooks"
    # Path fragment marking a command as an ai-hats project hook — short segment
    # so it also matches a bare-relative or absolute-path leak (HATS-961).
    _LEAKED_PROJECT_HOOK_MARKER = "ai-hats/library/hooks/"

    def ensure_runtime_hooks(
        self, project_dir: Path, result: CompositionResult | None = None, **kwargs
    ) -> None:
        """HATS-1170: Managed runtime hooks are written to session cache settings via
        build_session_artifacts, NOT to project-root .claude/settings.json.
        """
        pass

    def runtime_wiring_changes(
        self, project_dir: Path, result: CompositionResult | None = None
    ) -> list[tuple[str, str]]:
        """HATS-1170: Project-root .claude/settings.json is no longer written or tracked."""
        return []

    @staticmethod
    def _runtime_wiring_name(tag: str, desired_by_tag: dict[str, dict]) -> str:
        """Human display name for a managed wiring tag — the script basename when
        still desired, else the skill segment of the ``ai-hats:<skill>:…`` tag."""
        entry = desired_by_tag.get(tag)
        if entry:
            cmd = (entry.get(ClaudeProvider._SETTINGS_HOOKS_KEY) or [{}])[0].get("command", "")
            base = str(cmd).rsplit("/", 1)[-1]
            if base:
                return base
        parts = tag.split(":")
        return parts[1] if len(parts) > 1 else tag

    def _desired_runtime_entries(
        self, project_dir: Path, result: CompositionResult | None
    ) -> dict[str, list[dict]]:
        """``{event: [managed entry, ...]}`` the composition should produce.

        The guard is unconditional; skill hooks are added only when ``result``
        is present and the declared script resolves (a hook whose script
        cannot be found is skipped — the materialize step skips it too, so
        settings.json never points at a file that will not exist).
        """

        def rel(path: Path) -> str:
            # Claude Code resolves a relative PreToolUse ``command`` against the
            # agent's cwd, NOT the project root — a bare relative path fails
            # (exit 127) when a session / sub-agent starts in a subdirectory.
            # Prefix with $CLAUDE_PROJECT_DIR (expanded at hook-execution time)
            # so the command resolves regardless of cwd. Absolute fallback for
            # hooks that live outside the project tree.
            try:
                return CLAUDE_PROJECT_DIR_VAR + str(path.relative_to(project_dir))
            except ValueError:
                return str(path)

        lib = _lib_hooks_dir(project_dir)
        if not lib.resolve().is_relative_to(project_dir.resolve()):
            # HATS-897: warn, don't skip — bare out-of-tree AI_HATS_DIR is legit (HATS-380)
            warnings.warn(
                f"runtime hook commands will be written to settings.json as "
                f"absolute paths outside the project: {lib} (AI_HATS_DIR "
                f"override in effect). If this env leaked from another "
                f"project's session, unset it and re-run (HATS-897).",
                stacklevel=2,
            )
        desired: dict[str, list[dict]] = {}

        guard = lib / "pre_bash_shared_state_guard.sh"
        desired.setdefault(HOOK_PRE_TOOL_USE, []).append(
            {
                "matcher": "Bash",
                "_ai_hats_managed": self._MANAGED_HOOK_TAG,
                self._SETTINGS_HOOKS_KEY: [{"type": "command", "command": rel(guard)}],
            }
        )

        if result is None:
            return desired

        for event, entries in collect_runtime_hooks(result).items():
            for skill_name, hook in entries:
                if resolve_skill_script(result, skill_name, hook.script) is None:
                    continue
                command = rel(lib / managed_runtime_hook_filename(skill_name, hook.script))
                desired.setdefault(event, []).append(
                    {
                        "matcher": hook.matcher,
                        "_ai_hats_managed": f"ai-hats:{skill_name}:{event}:{hook.matcher}",
                        self._SETTINGS_HOOKS_KEY: [{"type": "command", "command": command}],
                    }
                )
        return desired

    @staticmethod
    def _upsert_managed_entry(event_list: list, want: dict) -> bool:
        """Insert / update one managed entry in ``event_list``. Returns True
        if the list changed.

        1. An existing entry carrying the same managed tag → update in place
           (or no-op if already identical).
        2. Else, if a user-authored entry already wires the same script
           basename → respect it (no managed dup, avoid double-firing).
        3. Else append.
        """
        tag = want["_ai_hats_managed"]
        for i, entry in enumerate(event_list):
            if isinstance(entry, dict) and entry.get("_ai_hats_managed") == tag:
                if entry == want:
                    return False
                event_list[i] = want
                return True

        want_basename = want[ClaudeProvider._SETTINGS_HOOKS_KEY][0]["command"].rsplit("/", 1)[-1]
        for entry in event_list:
            if not isinstance(entry, dict) or entry.get("_ai_hats_managed"):
                continue
            for hook in entry.get(ClaudeProvider._SETTINGS_HOOKS_KEY, []) or []:
                if not isinstance(hook, dict):
                    continue
                # Exact basename match — NOT endswith. A user file whose name
                # merely ends with ours (e.g. ``my_pre_bash_shared_state_guard.sh``)
                # is a DIFFERENT script and must not suppress our managed entry
                # (that would silently drop the HATS-437 guard). rsplit drops any
                # ``$CLAUDE_PROJECT_DIR/`` / directory prefix.
                if str(hook.get("command", "")).rsplit("/", 1)[-1] == want_basename:
                    return False  # user already wired this exact script — respect it

        event_list.append(want)
        return True

    @staticmethod
    def _sweep_stale_managed_tags(hooks_root: dict, desired_tags: set[str]) -> set[str]:
        """Drop ai-hats-managed entries no longer in ``desired_tags`` from every
        event list and return the removed tags (HATS-833). Preserves
        user-authored entries and still-desired managed ones; cascade-drops an
        event key whose list becomes empty.
        """
        removed: set[str] = set()
        for event in list(hooks_root.keys()):
            event_list = hooks_root[event]
            if not isinstance(event_list, list):
                continue
            kept: list = []
            for entry in event_list:
                if (
                    isinstance(entry, dict)
                    and isinstance(entry.get("_ai_hats_managed"), str)
                    and entry["_ai_hats_managed"].startswith("ai-hats:")
                    and entry["_ai_hats_managed"] not in desired_tags
                ):
                    removed.add(entry["_ai_hats_managed"])
                else:
                    kept.append(entry)
            if len(kept) != len(event_list):
                if kept:
                    hooks_root[event] = kept
                else:
                    del hooks_root[event]
        return removed

    @staticmethod
    def _sweep_stale_managed(hooks_root: dict, desired_tags: set[str]) -> bool:
        """Bool back-compat wrapper over :meth:`_sweep_stale_managed_tags`."""
        return bool(ClaudeProvider._sweep_stale_managed_tags(hooks_root, desired_tags))

    def build_meta_prompt(
        self,
        result: "CompositionResult",
        project_dir: "Path",
        ticket_context: str,
        linked_context: str,
        task: str,
    ) -> str:
        from .sdk_options import _build_system_prompt, build_first_user_message
        
        sp = _build_system_prompt(result, project_dir, self)
        system_text = sp.get("append", "")
        initial_message = build_first_user_message(
            ticket_context=ticket_context,
            linked_context=linked_context,
            task=task,
        )
        return (
            "==== SDK system_prompt (preset=claude_code, append) ====\n"
            f"{system_text}\n"
            "\n"
            "==== SDK first user message ====\n"
            f"{initial_message}\n"
        )

    def leaked_user_global_project_hooks(self, home: "Path") -> list[str]:
        """ai-hats project-hook commands leaked into ``<home>/.claude/settings.json``.

        Any ai-hats hook in user-global settings is a leak (double-fires + 404s
        off project-root, HATS-961). Matched by command substring — not the
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
                    if self._LEAKED_PROJECT_HOOK_MARKER in command:
                        leaked.append(command)
        return leaked

    def settings_lint_warnings(self, project_dir: Path) -> list[str]:
        """One warning per deprecated permission rule in the Claude settings
        chain (user-global + project + local). Warn-only — the settings files
        are user-owned and never mutated (HATS-1006)."""
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


class ClaudeSubagentEngine(SubagentEngine):
    def __init__(self, provider: ClaudeProvider) -> None:
        self._provider = provider

    def run(
        self,
        *,
        result: "CompositionResult",
        project_dir: Path,
        work_dir: Path,
        session_id: str,
        task: str,
        ticket_id: str,
        env: dict[str, str],
        model: str | None,
        timeout_s: int,
        artifacts: BuiltArtifacts | None = None,
    ) -> ProviderRunResult:
        if artifacts is None:
            artifacts = self._provider.build_session_artifacts(
                project_dir, result, session_id, run_mode="automate",
                artifacts=BuiltArtifacts(),
            )
        sys_prompt = artifacts.sdk_options.get("system_prompt")
        plugins = artifacts.sdk_options.get("plugins")
        opts = build_options(
            composition_result=result,
            provider=self._provider,
            project_dir=project_dir,
            session_id=session_id,
            work_dir=work_dir,
            model=model or "",
            settings=artifacts.sdk_options.get("settings"),
            setting_sources=artifacts.sdk_options.get("setting_sources"),
            extra_env=env,
            system_prompt=sys_prompt,
            plugins=plugins,
        )
        msg = build_first_user_message(
            task=task,
            ticket_context=f"Ticket: {ticket_id}" if ticket_id else "",
        )
        run_res = sdk_runner.run_claude_sdk_blocking(opts, msg, timeout_s=timeout_s)



        return ProviderRunResult(
            exit_code=run_res.exit_code,
            stdout=run_res.stdout,
            stderr=run_res.stderr,
            timed_out=run_res.timed_out,
            error=run_res.error,
            session_id=run_res.claude_session_id,
            total_cost_usd=run_res.total_cost_usd,
            num_turns=run_res.num_turns,
            stop_reason=run_res.stop_reason,
        )
