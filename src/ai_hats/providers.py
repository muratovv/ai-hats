"""Provider abstraction — adapters for Gemini and Claude CLI."""

from __future__ import annotations

import abc
import json
import shutil
from pathlib import Path

from .composer import (
    CompositionResult,
    ResolvedComponent,
    collect_runtime_hooks,
    resolve_skill_script,
)
from .paths import hooks_dir as _lib_hooks_dir
from .paths import managed_runtime_hook_filename
from .paths import session_cache_dir
from .placeholders import expand_path_placeholders


INJECTION_START = "<!-- AI-HATS:START -->"
INJECTION_END = "<!-- AI-HATS:END -->"

# HATS-284: lowercase scaffold markers — used in `./CLAUDE.md` to delimit the
# user-owned ai-hats block. PUBLISH_AGGREGATOR_* names kept for backwards
# compatibility of imports across the codebase; functionally these are the
# scaffold markers.
PUBLISH_AGGREGATOR_START = "<!-- ai-hats:start -->"
PUBLISH_AGGREGATOR_END = "<!-- ai-hats:end -->"

# Always-on rules that stay in prompt (safety-critical + framework invariants).
ALWAYS_ON_RULES = {
    "global_rule_destructive_actions",
    "global_rule_resource_hygiene",
    "dev_rule_secure_coding",
    "dev_rule_tool_call_hygiene",
    # HATS-437: primary defense against autonomous shared-state writes.
    # The PreToolUse / pre-push hooks are a safety net for this rule.
    "rule_pause_before_shared_state_write",
    # HATS-452: framework-invariant reminder for any agent that may
    # touch composition / pipeline / runtime internals. Short body
    # (~600 chars); acceptable budget for an always-on architectural
    # guard. Full rationale: docs/adr/0005-*.md.
    "rule_composition_value_contract",
}


def _extract_frontmatter_description(skill: ResolvedComponent) -> str:
    """Extract description from SKILL.md YAML frontmatter."""
    skill_md = skill.source_path / "SKILL.md"
    if not skill_md.exists():
        return skill.name
    text = skill_md.read_text()
    if not text.startswith("---"):
        return skill.name
    end = text.find("---", 3)
    if end == -1:
        return skill.name
    for line in text[3:end].splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    return skill.name


class Provider(abc.ABC):
    """Abstract provider interface."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @abc.abstractmethod
    def system_prompt_path(self, project_dir: Path) -> Path:
        """Path to the system prompt file for this provider."""

    @abc.abstractmethod
    def rules_dir(self, session_dir: Path) -> Path:
        """Directory where rules files should be placed."""

    @abc.abstractmethod
    def build_system_prompt(self, result: CompositionResult) -> str:
        """Build the complete system prompt from composition result."""

    @abc.abstractmethod
    def inject_rules(self, session_dir: Path, rules: list[ResolvedComponent]) -> None:
        """Copy rule files into the provider's expected location."""

    @abc.abstractmethod
    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        """Get the CLI command to launch this provider."""

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
        *,
        model: str | None = None,
    ) -> list[str]:
        """Build a non-interactive command that runs ``meta_prompt`` through this provider.

        Default: return ``cmd`` unchanged. Subclasses tailor the invocation
        to their CLI (e.g. Claude needs ``--print -p``, Gemini needs ``-p``).
        ``model`` is an optional explicit model name; when None, the provider
        CLI's default applies (back-compat).
        """
        return cmd

    @abc.abstractmethod
    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        """Get environment variables needed for the provider."""

    def build_session_prompt(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        """Build CLI args and env vars for a per-session composed prompt.

        Called for EVERY session (default role and explicit ``--role`` alike).
        ``session_id`` keys the per-session cache dir under
        ``<ai_hats_dir>/.cache/sessions/<session_id>/`` — provider writes the
        prompt file and plugin-dir there. Caller owns dir cleanup at
        session_end (``_cleanup_session_cache`` in runtime.py).

        Returns ``(extra_args, extra_env, meta_prompt)``. ``meta_prompt`` is
        the EXACT bytes that the provider will see as system-prompt override
        (HATS-523: persisted to ``<session_dir>/meta_prompt.txt`` by
        ``WrapRunner.run`` for post-hoc audit / regression detection,
        symmetric with ``SubAgentRunner.run``). Empty string when the
        provider has no system-prompt channel.

        Default: no-op (subclasses override).
        """
        return [], {}, ""

    def materialize_runtime_skills(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> list[str]:
        """Materialize the composed role's skills for runtime discovery.

        HATS-307: returns extra CLI args (e.g. ``["--plugin-dir", <path>]``)
        that make the spawned provider session see the role's skills via its
        own Skill registry. ``session_id`` keys the cache dir; plugin lives
        at ``<cache_dir>/plugin/`` and is cleaned with the whole cache dir
        at session_end.

        Default: no-op — the provider has no per-spawn skill materialization
        mechanism (Gemini case — see HATS-367 follow-up).
        """
        del project_dir, result, session_id
        return []

    def scaffold_template_relpath(self) -> str | None:
        """Library-relative path to the provider's prompt-file scaffold template.

        Default: None — provider has no scaffold (e.g. Gemini per HATS-276).
        Subclasses point at a markdown asset under
        `libraries/templates/<provider>/...`.
        """
        return None

    def ensure_runtime_hooks(
        self, project_dir: Path, result: CompositionResult | None = None
    ) -> None:
        """Install provider-specific runtime hooks (e.g. Claude Code PreToolUse).

        Called by ``Assembler._refresh`` after the provider scaffold is
        ensured. Idempotent — safe to invoke on every role apply.

        ``result`` is the active role's composition (``None`` on the legacy
        bare-bump path with no active role); ``ClaudeProvider`` reads the
        skills' ``runtime_hooks:`` declarations from it (HATS-597).

        Default: no-op. Providers without a runtime-hook channel (Gemini)
        rely on the rule layer plus skill-contributed git hooks.

        HATS-437: ClaudeProvider overrides to write a PreToolUse entry
        for ``library/hooks/pre_bash_shared_state_guard.sh`` into
        ``.claude/settings.json``, plus any skill-declared runtime hooks.
        """
        del project_dir, result
        return None

    def update_system_prompt(self, project_dir: Path, content: str) -> None:
        """Write or update the inline system prompt block.

        Used by providers without a scaffold (e.g. Gemini) to maintain the
        AI-HATS-managed section of `./GEMINI.md` between `INJECTION_START` /
        `INJECTION_END` markers. For providers that declare a scaffold
        (Claude — HATS-284), this method is dormant: `Assembler.set_role`
        skips the call entirely (HATS-286), and the lowercase-marker early
        return below provides a defense-in-depth no-op if it is invoked
        anyway.
        """
        from .safe_delete import replace as _safe_replace

        prompt_path = self.system_prompt_path(project_dir)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)

        if prompt_path.exists():
            existing = prompt_path.read_text()
            # HATS-284: lowercase scaffold markers signal the project is on
            # the canonical-publish layout — `./CLAUDE.md` is user-owned and
            # the framework injection lives in `.claude/CLAUDE.md`.
            if PUBLISH_AGGREGATOR_START in existing and PUBLISH_AGGREGATOR_END in existing:
                return
            if INJECTION_START in existing and INJECTION_END in existing:
                # Update between markers, preserve everything outside
                before = existing[: existing.index(INJECTION_START)]
                after = existing[existing.index(INJECTION_END) + len(INJECTION_END) :]
                new_content = f"{before}{INJECTION_START}\n{content}\n{INJECTION_END}{after}"
                _safe_replace(
                    prompt_path, new_content.encode("utf-8"),
                    reason="system-prompt", project_dir=project_dir,
                )
                return
            if existing.strip():
                # Existing file without markers — preserve as project context
                _safe_replace(
                    prompt_path,
                    f"{INJECTION_START}\n{content}\n{INJECTION_END}\n\n{existing}".encode("utf-8"),
                    reason="system-prompt", project_dir=project_dir,
                )
                return

        # Fresh write with markers
        _safe_replace(
            prompt_path,
            f"{INJECTION_START}\n{content}\n{INJECTION_END}\n".encode("utf-8"),
            reason="system-prompt", project_dir=project_dir,
        )


class GeminiProvider(Provider):
    @property
    def name(self) -> str:
        return "gemini"

    def system_prompt_path(self, project_dir: Path) -> Path:
        return project_dir / "GEMINI.md"

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        sections = []

        if result.priorities:
            sections.append(
                "## PRIORITIES\n"
                + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(result.priorities))
            )

        if result.merged_injection:
            sections.append(result.merged_injection)

        # Only always-on rules in prompt
        always_on = [r for r in result.rules if r.name in ALWAYS_ON_RULES]
        if always_on:
            rules_section = "## RULES\n"
            for rule in always_on:
                if rule.injection:
                    rules_section += f"\n### {rule.name}\n{rule.injection}\n"
            sections.append(rules_section)

        # Skills: index only (body loaded on demand via native provider)
        if result.skills:
            lines = ["## AVAILABLE SKILLS\n"]
            for skill in result.skills:
                desc = _extract_frontmatter_description(skill)
                lines.append(f"- **{skill.name}** — {desc}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    def inject_rules(self, session_dir: Path, rules: list[ResolvedComponent]) -> None:
        rules_dir = self.rules_dir(session_dir)
        rules_dir.mkdir(parents=True, exist_ok=True)
        for rule in rules:
            if rule.source_path.is_dir():
                dest = rules_dir / rule.name
                if dest.exists():
                    shutil.rmtree(dest)  # safe-delete: ok session-cache (per-session ephemeral republish)
                shutil.copytree(rule.source_path, dest)

    def build_session_prompt(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        """Create session-scoped rules dir with composed prompt.

        Uses GEMINI_CLI_PROJECT_RULES_PATH to inject without touching GEMINI.md.
        Rules dir lives under ``<ai_hats_dir>/.cache/sessions/<session_id>/rules/``
        and is cleaned with the whole cache dir at session_end.

        Third return element (HATS-523): ``prompt_content`` — the exact bytes
        written to ``00_MANDATORY_ROLE.md`` (after HATS-380 placeholder
        expansion). Persisted by ``WrapRunner`` to
        ``<session_dir>/meta_prompt.txt`` for post-hoc audit.
        """
        prompt_content = self.build_system_prompt(result)
        # HATS-380: expand placeholder before the prompt reaches the agent.
        prompt_content = expand_path_placeholders(prompt_content, project_dir)

        # Per-session cache dir (HATS-294): gitignored, swept by TTL.
        cache_dir = session_cache_dir(project_dir, session_id)
        rules_dir = cache_dir / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)

        # Copy existing project rules
        from .paths import rules_dir as _project_rules_dir

        project_rules = _project_rules_dir(project_dir)
        if project_rules.exists():
            for item in project_rules.iterdir():
                if item.is_dir():
                    shutil.copytree(item, rules_dir / item.name)

        # Write mandatory role override (00_ prefix = highest priority)
        (rules_dir / "00_MANDATORY_ROLE.md").write_text(prompt_content)

        return [], {"GEMINI_CLI_PROJECT_RULES_PATH": str(rules_dir)}, prompt_content

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["gemini"]
        if args:
            cmd.extend(args)
        return cmd

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
        *,
        model: str | None = None,
    ) -> list[str]:
        extra = ["--model", model] if model else []
        return cmd + extra + ["-p", meta_prompt]

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        return {
            "GEMINI_CLI_PROJECT_RULES_PATH": str(self.rules_dir(session_dir)),
        }


class ClaudeProvider(Provider):
    @property
    def name(self) -> str:
        return "claude"

    def system_prompt_path(self, project_dir: Path) -> Path:
        return project_dir / "CLAUDE.md"

    def scaffold_template_relpath(self) -> str | None:
        return "templates/claude/CLAUDE.md.template"

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        sections = []

        if result.priorities:
            sections.append(
                "## PRIORITIES\n"
                + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(result.priorities))
            )

        if result.merged_injection:
            sections.append(result.merged_injection)

        # Only always-on rules in prompt
        always_on = [r for r in result.rules if r.name in ALWAYS_ON_RULES]
        if always_on:
            rules_section = "## RULES\n"
            for rule in always_on:
                if rule.injection:
                    rules_section += f"\n### {rule.name}\n{rule.injection}\n"
            sections.append(rules_section)

        # Skills: index only (body loaded on demand via native provider)
        if result.skills:
            lines = ["## AVAILABLE SKILLS\n"]
            for skill in result.skills:
                desc = _extract_frontmatter_description(skill)
                lines.append(f"- **{skill.name}** — {desc}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    def inject_rules(self, session_dir: Path, rules: list[ResolvedComponent]) -> None:
        rules_dir = self.rules_dir(session_dir)
        rules_dir.mkdir(parents=True, exist_ok=True)
        for rule in rules:
            if rule.source_path.is_dir():
                dest = rules_dir / rule.name
                if dest.exists():
                    shutil.rmtree(dest)  # safe-delete: ok session-cache (per-session ephemeral republish)
                shutil.copytree(rule.source_path, dest)

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

        # Build full file content preserving project-local sections.
        # HATS-285: handle both legacy uppercase markers and the new lowercase
        # scaffold (which contains an @-import line that we replace with inline
        # override content for the duration of the session).
        existing_path = self.system_prompt_path(project_dir)
        if existing_path.exists():
            existing = existing_path.read_text()
            if PUBLISH_AGGREGATOR_START in existing and PUBLISH_AGGREGATOR_END in existing:
                before = existing[: existing.index(PUBLISH_AGGREGATOR_START)]
                after = existing[
                    existing.index(PUBLISH_AGGREGATOR_END) + len(PUBLISH_AGGREGATOR_END) :
                ]
                full_content = (
                    f"{before}{INJECTION_START}\n{prompt_content}\n{INJECTION_END}{after}"
                )
            elif INJECTION_START in existing and INJECTION_END in existing:
                before = existing[: existing.index(INJECTION_START)]
                after = existing[existing.index(INJECTION_END) + len(INJECTION_END) :]
                full_content = (
                    f"{before}{INJECTION_START}\n{prompt_content}\n{INJECTION_END}{after}"
                )
            else:
                full_content = f"{INJECTION_START}\n{prompt_content}\n{INJECTION_END}\n"
        else:
            full_content = f"{INJECTION_START}\n{prompt_content}\n{INJECTION_END}\n"

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

        return [
            "--system-prompt-file", str(override_file),
            *skill_args,
        ], {}, full_content

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
        from .plugin_dir import materialize_plugin_dir

        plugin_dir = session_cache_dir(project_dir, session_id) / "plugin"
        materialize_plugin_dir(result.name, result.skills, project_dir, plugin_dir)
        return ["--plugin-dir", str(plugin_dir)]

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["claude"]
        if args:
            cmd.extend(args)
        return cmd

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
        return {}

    # ----- HATS-437: PreToolUse hook auto-wire -----

    # Marker tag on managed PreToolUse entries. Lets ``ensure_runtime_hooks``
    # locate prior installs and update them in place rather than appending
    # a duplicate. User-authored entries (without the tag) are never touched.
    _MANAGED_HOOK_TAG = "ai-hats:hats-437"

    def ensure_runtime_hooks(
        self, project_dir: Path, result: CompositionResult | None = None
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
        settings_path = project_dir / ".claude" / "settings.json"

        desired = self._desired_runtime_entries(project_dir, result)
        desired_tags = {
            entry["_ai_hats_managed"]
            for entries in desired.values()
            for entry in entries
        }

        # Read existing settings, tolerating missing file / malformed JSON.
        data: dict = {}
        if settings_path.exists():
            try:
                raw = settings_path.read_text()
                if raw.strip():
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        # Settings file is not an object — bail to avoid clobbering.
                        return
            except json.JSONDecodeError:
                # Malformed user-owned settings. Leave alone.
                return

        hooks_root = data.setdefault("hooks", {})
        if not isinstance(hooks_root, dict):
            return  # user-shaped — do not clobber

        changed = False
        for event, want_entries in desired.items():
            event_list = hooks_root.setdefault(event, [])
            if not isinstance(event_list, list):
                continue  # user-shaped event — leave alone
            for want in want_entries:
                if self._upsert_managed_entry(event_list, want):
                    changed = True

        # Sweep managed entries no longer desired (across every event, so a
        # PostToolUse skill hook is swept too when its skill leaves).
        if self._sweep_stale_managed(hooks_root, desired_tags):
            changed = True

        if changed:
            self._write_settings(settings_path, data, project_dir)

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
            try:
                return str(path.relative_to(project_dir))
            except ValueError:
                return str(path)

        lib = _lib_hooks_dir(project_dir)
        desired: dict[str, list[dict]] = {}

        guard = lib / "pre_bash_shared_state_guard.sh"
        desired.setdefault("PreToolUse", []).append({
            "matcher": "Bash",
            "_ai_hats_managed": self._MANAGED_HOOK_TAG,
            "hooks": [{"type": "command", "command": rel(guard)}],
        })

        if result is None:
            return desired

        for event, entries in collect_runtime_hooks(result).items():
            for skill_name, hook in entries:
                if resolve_skill_script(result, skill_name, hook.script) is None:
                    continue
                command = rel(lib / managed_runtime_hook_filename(skill_name, hook.script))
                desired.setdefault(event, []).append({
                    "matcher": hook.matcher,
                    "_ai_hats_managed": f"ai-hats:{skill_name}:{event}:{hook.matcher}",
                    "hooks": [{"type": "command", "command": command}],
                })
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

        want_basename = want["hooks"][0]["command"].rsplit("/", 1)[-1]
        for entry in event_list:
            if not isinstance(entry, dict) or entry.get("_ai_hats_managed"):
                continue
            for hook in entry.get("hooks", []) or []:
                if isinstance(hook, dict) and str(hook.get("command", "")).endswith(
                    want_basename
                ):
                    return False  # user already wired it manually — respect that

        event_list.append(want)
        return True

    @staticmethod
    def _sweep_stale_managed(hooks_root: dict, desired_tags: set[str]) -> bool:
        """Drop ai-hats-managed entries no longer in ``desired_tags`` from every
        event list. Preserves user-authored entries and still-desired managed
        ones; cascade-drops an event key whose list becomes empty. Returns True
        if anything was removed.
        """
        changed = False
        for event in list(hooks_root.keys()):
            event_list = hooks_root[event]
            if not isinstance(event_list, list):
                continue
            kept = [
                entry
                for entry in event_list
                if not (
                    isinstance(entry, dict)
                    and isinstance(entry.get("_ai_hats_managed"), str)
                    and entry["_ai_hats_managed"].startswith("ai-hats:")
                    and entry["_ai_hats_managed"] not in desired_tags
                )
            ]
            if len(kept) != len(event_list):
                changed = True
                if kept:
                    hooks_root[event] = kept
                else:
                    del hooks_root[event]
        return changed

    @staticmethod
    def _write_settings(settings_path: Path, data: dict, project_dir: Path) -> None:
        from .safe_delete import replace as _safe_replace

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


PROVIDERS: dict[str, type[Provider]] = {
    "gemini": GeminiProvider,
    "claude": ClaudeProvider,
}


def get_provider(name: str) -> Provider:
    """Get a provider instance by name."""
    cls = PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown provider: {name}. Available: {list(PROVIDERS.keys())}")
    return cls()
