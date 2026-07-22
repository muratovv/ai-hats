"""Claude Code surface plugin for ai-hats."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

from ai_hats_core import CompositionResult, ResolvedComponent
from ai_hats_observe.parsers.claude import ClaudeParser
from ai_hats.providers import Provider, ProviderRunResult, SubagentEngine
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
from ai_hats.constants import HOOK_PRE_TOOL_USE, PROVIDER_CLAUDE
from ai_hats.providers import (
    INJECTION_START,
    INJECTION_END,
    PUBLISH_AGGREGATOR_START,
    PUBLISH_AGGREGATOR_END,
    _PERMISSION_ARRAYS,
    DEPRECATED_RULE_TOOLS,
)

if TYPE_CHECKING:
    from ai_hats_observe.parsers.base import TranscriptParser

@dataclass(frozen=True)
class SettingsFinding:
    """One deprecated permission rule: where it is and what replaces it."""

    source: Path
    array: str
    rule: str
    replacement: str

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
    def supports_sdk_engine(self) -> bool:
        return True
    @property
    def name(self) -> str:
        return PROVIDER_CLAUDE

    def transcript_parser(self) -> TranscriptParser:
        # HATS-948: Claude emits a structured JSONL session log → richer parse.
        return ClaudeParser()

    def system_prompt_path(self, project_dir: Path) -> Path:
        return claude_md(project_dir)

    def scaffold_template_relpath(self) -> str | None:
        return "templates/claude/CLAUDE.md.template"

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        # HATS-701: skills reach the agent via the native --plugin-dir (HITL)
        # / SDK plugin (sub-agent) registry materialized in build_session_prompt
        # / sdk_options. Suppress the AVAILABLE SKILLS index here to avoid the
        # 2-3x duplicate listing (~1.5k tok/session).
        return self._compose_sections(result, include_skills=False)

    def build_session_prompt(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        """Write composed prompt to per-session cache, pass via --system-prompt-file.

        HATS-294: prompt and plugin-dir both live under
        ``<ai_hats_dir>/.cache/sessions/<session_id>/`` so the whole session's
        ephemeral artefacts are colocated and cleaned in one rmtree at
        session_end.

        Preserves project-local content outside AI-HATS markers.

        Third return element (HATS-523): ``full_content`` — the exact bytes
        written to ``<cache>/sessions/<session_id>/prompt.md`` and passed via
        ``--system-prompt-file``. Persisted by ``WrapRunner`` to
        ``<session_dir>/meta_prompt.txt`` for post-hoc regression detection
        (HATS-452 / HATS-501 class) and e2e verification, symmetric with
        ``SubAgentRunner.save_meta_prompt``.
        """
        prompt_content = self.build_system_prompt(result)
        # HATS-380: expand placeholder before --system-prompt-file content
        # reaches the agent.
        prompt_content = expand_path_placeholders(prompt_content, project_dir)
        # HATS-625: expand <available_roles> with the live role catalog
        # (no-op unless the placeholder is present, e.g. the initial-wizard).
        prompt_content = expand_role_catalog(prompt_content, project_dir)

        full_content = self._build_full_content(project_dir, prompt_content)

        # HATS-294: write to per-session cache dir. The whole dir is
        # cleaned at session_end by _cleanup_session_cache in runtime.py,
        # which also sweeps orphans older than 24h on session_start.
        cache_dir = session_cache_dir(project_dir, session_id)
        cache_dir.mkdir(parents=True, exist_ok=True)
        override_file = cache_dir / "prompt.md"
        override_file.write_text(full_content)

        # HATS-307: materialize spawned role's skills into a plugin-dir under
        # the same cache dir so Claude Code's Skill tool can resolve them.
        skill_args = self.materialize_runtime_skills(project_dir, result, session_id)

        extra_env: dict[str, str] = {}
        plugin_skills_dir = cache_dir / "plugin" / "skills"
        inject_skill_paths_to_env(extra_env, result.skills, plugin_skills_dir)

        return (
            [
                "--system-prompt-file",
                str(override_file),
                *skill_args,
            ],
            extra_env,
            full_content,
        )


    def supports_sdk_engine(self) -> bool:
        """Indicates this provider uses the Python SDK path."""
        return True

    def engine(self) -> "SubagentEngine | None":
        return ClaudeSubagentEngine(self)

    def _build_full_content(self, project_dir: Path, prompt_content: str) -> str:
        """Build full file content preserving project-local sections."""
        # HATS-285: handle both legacy uppercase markers and the new lowercase
        # scaffold (which contains an @-import line that we replace with inline
        # override content for the duration of the session).
        existing_path = self.system_prompt_path(project_dir)
        if not existing_path.exists():
            return f"{INJECTION_START}\n{prompt_content}\n{INJECTION_END}\n"
        
        existing = existing_path.read_text()
        if PUBLISH_AGGREGATOR_START in existing and PUBLISH_AGGREGATOR_END in existing:
            before = existing[: existing.index(PUBLISH_AGGREGATOR_START)]
            after = existing[
                existing.index(PUBLISH_AGGREGATOR_END) + len(PUBLISH_AGGREGATOR_END) :
            ]
            return f"{before}{INJECTION_START}\n{prompt_content}\n{INJECTION_END}{after}"
        
        if INJECTION_START in existing and INJECTION_END in existing:
            before = existing[: existing.index(INJECTION_START)]
            after = existing[existing.index(INJECTION_END) + len(INJECTION_END) :]
            return f"{before}{INJECTION_START}\n{prompt_content}\n{INJECTION_END}{after}"
        
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
        from ai_hats.plugin_dir import materialize_plugin_dir

        plugin_dir = session_cache_dir(project_dir, session_id) / "plugin"
        materialize_plugin_dir(result.name, result.skills, project_dir, plugin_dir)
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
        """Install / refresh ai-hats-managed runtime-hook entries in
        ``.claude/settings.json``. Idempotent.

        Writes two kinds of managed entry, keyed by the native Claude event:

        * the HATS-437 shared-state guard (PreToolUse, tag
          ``ai-hats:hats-437``) — always, until HATS-598 migrates it onto the
          registry;
        * one entry per ``(event, skill, matcher)`` a composed skill declares
          under ``runtime_hooks:`` (HATS-597), tag
          ``ai-hats:<skill>:<event>:<matcher>``, ``command`` = the
          :func:`managed_runtime_hook_filename` path the assembler
          materializes.

        Each managed entry is located by its tag and updated in place; a
        user-authored entry already wiring the same script is respected (no
        dup); managed entries no longer desired (e.g. a skill left the role)
        are swept. User-authored entries (no ``ai-hats:`` tag) are never
        touched. Commands are written project-relative so the config survives
        ``project_dir`` moves; ``ai-hats self update``'s migration_healer owns
        the physical-move case.
        """
        settings_path, data, changed, _tags, _desired = self._plan_runtime_hooks(
            project_dir, result
        )
        if changed and data is not None:
            self._write_settings(settings_path, data, project_dir)

    def _plan_runtime_hooks(
        self, project_dir: Path, result: CompositionResult | None
    ) -> tuple[Path, dict | None, bool, set[str], dict[str, dict]]:
        """Compute the desired managed runtime-hook wiring against the current
        ``.claude/settings.json`` WITHOUT writing (HATS-833).

        Single source of truth shared by :meth:`ensure_runtime_hooks` (which
        writes the returned ``data`` when ``changed``) and
        :meth:`runtime_wiring_changes` (which discards it and reports the changed
        tags). Running the SAME upsert/sweep on a freshly-parsed copy guarantees
        the detector can never diverge from the writer — in particular it inherits
        :meth:`_upsert_managed_entry`'s respect for a user-authored entry already
        wiring the same script (so a covered hook is never flagged drift forever).

        Returns ``(settings_path, data, changed, changed_tags, desired_by_tag)``;
        ``data`` is ``None`` when the file is user-shaped / malformed and must be
        left untouched (then ``changed`` is False, ``changed_tags`` empty).
        """
        settings_path = claude_settings_json(project_dir)
        desired = self._desired_runtime_entries(project_dir, result)
        desired_by_tag = {
            entry["_ai_hats_managed"]: entry for entries in desired.values() for entry in entries
        }
        desired_tags = set(desired_by_tag)

        # Read existing settings, tolerating missing file / malformed JSON.
        data: dict = {}
        if settings_path.exists():
            try:
                raw = settings_path.read_text()
                if raw.strip():
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        # Settings file is not an object — bail to avoid clobbering.
                        return settings_path, None, False, set(), desired_by_tag
            except json.JSONDecodeError:
                # Malformed user-owned settings. Leave alone.
                return settings_path, None, False, set(), desired_by_tag

        hooks_root = data.setdefault(self._SETTINGS_HOOKS_KEY, {})
        if not isinstance(hooks_root, dict):
            return settings_path, None, False, set(), desired_by_tag  # user-shaped

        changed_tags: set[str] = set()
        for event, want_entries in desired.items():
            event_list = hooks_root.setdefault(event, [])
            if not isinstance(event_list, list):
                continue  # user-shaped event — leave alone
            for want in want_entries:
                if self._upsert_managed_entry(event_list, want):
                    changed_tags.add(want["_ai_hats_managed"])

        # Sweep managed entries no longer desired (across every event, so a
        # PostToolUse skill hook is swept too when its skill leaves).
        changed_tags |= self._sweep_stale_managed_tags(hooks_root, desired_tags)

        return settings_path, data, bool(changed_tags), changed_tags, desired_by_tag

    def runtime_wiring_changes(
        self, project_dir: Path, result: CompositionResult | None = None
    ) -> list[tuple[str, str]]:
        """Managed settings.json wiring drift as ``[(display_name, "wiring")]``.

        Reuses :meth:`_plan_runtime_hooks` (no write) so it is exactly the set of
        managed entries ``ensure_runtime_hooks`` would (re)write or sweep.
        """
        _path, _data, _changed, changed_tags, desired_by_tag = self._plan_runtime_hooks(
            project_dir, result
        )
        return [
            (self._runtime_wiring_name(tag, desired_by_tag), "wiring")
            for tag in sorted(changed_tags)
        ]

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

    @staticmethod
    def _write_settings(settings_path: Path, data: dict, project_dir: Path) -> None:
        from ai_hats_core.safe_delete import replace as _safe_replace

        settings_path.parent.mkdir(parents=True, exist_ok=True)
        # HATS-470: .claude/settings.json is a user-owned file (carries
        # the user's PreToolUse hooks). Snapshot via safe_delete.replace
        # so a bad ai-hats overwrite is recoverable from trash.
        _safe_replace(
            settings_path,
            (json.dumps(data, indent=2) + "\n").encode("utf-8"),
            reason="claude-settings",
            project_dir=project_dir,
        )

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
    ) -> ProviderRunResult:
        opts = build_options(
            composition_result=result,
            provider=self._provider,
            project_dir=project_dir,
            session_id=session_id,
            work_dir=work_dir,
            model=model or "",
            extra_env=env,
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
