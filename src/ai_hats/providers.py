"""Provider abstraction — adapters for Agy and Claude CLI."""

from __future__ import annotations

import abc
import contextlib
import json
import logging
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

from ai_hats_core import CompositionResult, ResolvedComponent
from ai_hats_observe.parsers.claude import ClaudeParser
from ai_hats_observe.parsers.trace import TraceParser

if TYPE_CHECKING:
    from ai_hats_observe.parsers.base import TranscriptParser

from .hook_collection import (
    collect_runtime_hooks,
    resolve_skill_script,
)
from .frontmatter import FrontmatterError, read_frontmatter
from .paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
from .paths import CLAUDE_PROJECT_DIR_VAR
from .paths import ai_hats_dir
from .paths import claude_md, claude_settings_json, claude_settings_local_json
from .paths import claude_user_settings_json
from .paths import hooks_dir as _lib_hooks_dir
from .paths import managed_runtime_hook_filename
from .paths import session_cache_dir
from .placeholders import expand_path_placeholders
from .provider_entry_points import (
    _is_first_party_entry_point,
    _provider_entry_points,
)
from .resolver import read_rule_body
from .role_catalog import expand_role_catalog
from . import owners


logger = logging.getLogger(__name__)

# HATS-905: retiring the managed settings.json hooks mechanism = dropping this
# line; the unclaimed-marker sweeper then strips ai-hats:* tagged entries.
owners.register_owner("runtime-hooks", module=__name__)

INJECTION_START = "<!-- AI-HATS:START -->"
INJECTION_END = "<!-- AI-HATS:END -->"

# HATS-284: lowercase scaffold markers — used in `./CLAUDE.md` to delimit the
# user-owned ai-hats block. PUBLISH_AGGREGATOR_* names kept for backwards
# compatibility of imports across the codebase; functionally these are the
# scaffold markers.
PUBLISH_AGGREGATOR_START = "<!-- ai-hats:start -->"
PUBLISH_AGGREGATOR_END = "<!-- ai-hats:end -->"

# HATS-865: definition moved to the constants leaf; re-exported here for the
# existing `from ai_hats.providers import ALWAYS_ON_RULES` importers.
from .constants import (  # noqa: E402
    ALWAYS_ON_RULES,
    HOOK_PRE_TOOL_USE,
    PROVIDER_CLAUDE,
)


def _extract_frontmatter_description(skill: ResolvedComponent) -> str:
    """Extract ``description`` from a skill's SKILL.md frontmatter, else its name.

    Best-effort: a malformed block warns and falls back to the name rather than
    crashing the prompt build for one skill — the loud raise is the hook path's
    job (HATS-814).
    """
    try:
        data = read_frontmatter(skill.source_path / "SKILL.md")
    except FrontmatterError as exc:
        logger.warning(
            "skill %r: malformed SKILL.md frontmatter; using name in the skill index: %s",
            skill.name,
            exc,
        )
        return skill.name
    desc = data.get("description")
    return desc if isinstance(desc, str) and desc else skill.name



import abc
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .composition_types import CompositionResult


@dataclass
class ProviderRunResult:
    exit_code: int
    session_id: str | None
    total_cost_usd: float
    num_turns: int
    stop_reason: str | None
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None


class SubagentEngine(abc.ABC):
    """Abstract engine for executing a subagent."""

    @abc.abstractmethod
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
        pass


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

    @contextlib.contextmanager
    def execution_context(self, project_dir: Path) -> contextlib.AbstractContextManager[None]:
        """Context manager active around provider CLI execution.

        Subclasses override to perform workspace setup/teardown during launch.
        """
        yield

    @abc.abstractmethod
    def build_system_prompt(self, result: CompositionResult) -> str:
        """Build the complete system prompt from composition result."""

    def transcript_parser(self) -> TranscriptParser:
        """The parser ``AuditWriter`` uses for this surface's session record.

        HATS-948: the parser rides the provider (no separate registry). Default
        is trace-only; a surface with a structured session log (Claude JSONL)
        overrides with a richer parser.
        """
        return TraceParser()

    def resolve_transcript(
        self, project_dir: Path, session_id: str, *, provider_session_id: str | None = None
    ) -> Path | None:
        """Resolve the path to this surface's structured session transcript.

        HATS-1087: ``transcript_parser`` knows HOW to parse; this knows WHERE
        to find the file. Default None — no structured transcript → the
        trace-log fallback (TraceParser on ``session.trace_path``). A surface
        with a structured session log (Claude JSONL, cline ``.messages.json``)
        overrides to discover it.
        """
        return None

    def leaked_user_global_project_hooks(self, home: Path) -> list[str]:
        """ai-hats project-hook commands this surface leaked into user-global config.

        ai-hats wires hooks only into *project* config; a copy in user-global
        config double-fires and 404s off project-root (HATS-961). Base surfaces
        manage no user-global hooks → none; ClaudeProvider overrides to scan
        ``~/.claude/settings.json``.
        """
        return []

    def settings_lint_warnings(self, project_dir: Path) -> list[str]:
        """Known surface-settings pitfalls to surface at session start (HATS-1006).

        Base surfaces lint nothing; ClaudeProvider overrides to check the Claude
        settings chain for permission rules the CLI has deprecated.
        """
        return []

    def _compose_sections(self, result: CompositionResult, *, include_skills: bool) -> str:
        """Assemble the shared system-prompt sections.

        Order: PRIORITIES → merged role/trait injection → always-on RULES →
        optional AVAILABLE SKILLS index.

        ``include_skills`` is the provider-specific toggle (HATS-701). Agy
        passes ``True`` — it has no native skill registry, so this index is
        its only discovery channel. Claude passes ``False`` — it materializes
        skills as a ``--plugin-dir`` (HITL) / SDK plugin (sub-agent) registry
        that already lists every skill with its full description, so emitting
        the index here would be a 2-3x duplicate (~1.5k tok/session).
        """
        sections: list[str] = []

        if result.priorities:
            sections.append(
                "## PRIORITIES\n"
                + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(result.priorities))
            )

        if result.merged_injection:
            sections.append(result.merged_injection)

        # Only always-on rules in prompt; body read on demand from source_path
        # (HATS-700 — composer no longer eager-loads rule bodies).
        always_on = [r for r in result.rules if r.name in ALWAYS_ON_RULES]
        if always_on:
            rules_section = "## RULES\n"
            for rule in always_on:
                body = read_rule_body(rule.source_path)
                if body:
                    rules_section += f"\n### {rule.name}\n{body}\n"
            sections.append(rules_section)

        # Skills: index only (body loaded on demand via native provider).
        if include_skills and result.skills:
            lines = ["## AVAILABLE SKILLS\n"]
            for skill in result.skills:
                desc = _extract_frontmatter_description(skill)
                lines.append(f"- **{skill.name}** — {desc}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    @abc.abstractmethod
    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        """Get the CLI command to launch this provider."""

    def get_cli_launch_args(
        self, base_cmd: list[str], session_id: str, is_resume: bool
    ) -> list[str]:
        """Provider-specific launch flags (e.g. session-id linkage).

        Default: none. ``wrap_runner`` calls this on EVERY provider, so a
        claude-only override left agy/cline/gemini raising AttributeError
        before launch (HATS-1130).
        """
        return base_cmd

    def model_flags(self, model: str) -> list[str]:
        """Convert a model name into provider-specific CLI flags."""
        return ["--model", model]

    def supports_sdk_engine(self) -> bool:
        """Whether this provider provides a native SDK SubagentEngine."""
        return False

    def engine(self) -> SubagentEngine | None:
        """Get the native SDK SubagentEngine for this provider."""
        return None

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
    ) -> list[str]:
        """Build a non-interactive command that runs ``meta_prompt`` through this provider.

        Default: return ``cmd`` unchanged. Subclasses tailor the invocation
        to their CLI (e.g. Claude needs ``--print -p``, Agy needs ``-p``).
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
        mechanism (Agy case — see HATS-367 follow-up).
        """
        del project_dir, result, session_id
        return []

    def scaffold_template_relpath(self) -> str | None:
        """Library-relative path to the provider's prompt-file scaffold template.

        Default: None — provider has no scaffold (e.g. Agy per HATS-276).
        Subclasses point at a markdown asset under
        `libraries/templates/<provider>/...`.
        """
        return None

    def ensure_runtime_hooks(
        self, project_dir: Path, result: CompositionResult | None = None, **kwargs
    ) -> None:
        """Install provider-specific runtime hooks (e.g. Claude Code PreToolUse).

        Called by ``Assembler._refresh`` after the provider scaffold is
        ensured. Idempotent — safe to invoke on every role apply.

        ``result`` is the active role's composition (``None`` on the legacy
        bare-bump path with no active role); ``ClaudeProvider`` reads the
        skills' ``runtime_hooks:`` declarations from it (HATS-597).

        Default: no-op. Providers without a runtime-hook channel (Agy)
        rely on the rule layer plus skill-contributed git hooks.

        HATS-437: ClaudeProvider overrides to write a PreToolUse entry
        for ``library/hooks/pre_bash_shared_state_guard.sh`` into
        ``.claude/settings.json``, plus any skill-declared runtime hooks.
        """
        del project_dir, result
        return None

    def runtime_wiring_changes(
        self, project_dir: Path, result: CompositionResult | None = None
    ) -> list[tuple[str, str]]:
        """Managed runtime-hook wiring drift as ``[(name, "wiring")]``. Default:
        none (no settings.json channel); ``ClaudeProvider`` overrides (HATS-833)."""
        del project_dir, result
        return []

    def update_system_prompt(self, project_dir: Path, content: str) -> None:
        """Write or update the inline system prompt block.

        Used by providers without a scaffold (e.g. Agy) to maintain the
        AI-HATS-managed section of `./GEMINI.md` between `INJECTION_START` /
        `INJECTION_END` markers. For providers that declare a scaffold
        (Claude — HATS-284), this method is dormant: `Assembler.set_role`
        skips the call entirely (HATS-286), and the lowercase-marker early
        return below provides a defense-in-depth no-op if it is invoked
        anyway.
        """
        from ai_hats_core.safe_delete import replace as _safe_replace

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
                    prompt_path,
                    new_content.encode("utf-8"),
                    reason="system-prompt",
                    project_dir=project_dir,
                )
                return
            if existing.strip():
                # Existing file without markers — preserve as project context
                _safe_replace(
                    prompt_path,
                    f"{INJECTION_START}\n{content}\n{INJECTION_END}\n\n{existing}".encode("utf-8"),
                    reason="system-prompt",
                    project_dir=project_dir,
                )
                return

        # Fresh write with markers
        _safe_replace(
            prompt_path,
            f"{INJECTION_START}\n{content}\n{INJECTION_END}\n".encode("utf-8"),
            reason="system-prompt",
            project_dir=project_dir,
        )


# ----- HATS-1006 Claude settings lint (docs/session-start-notices.md) -----

# Claude Code >=2.1.210: file-permission checks match only Edit()/Read() rules.
DEPRECATED_RULE_TOOLS: tuple[tuple[str, str], ...] = (
    ("Write", "Edit"),
    ("NotebookEdit", "Edit"),
    ("Glob", "Read"),
)

_PERMISSION_ARRAYS = ("allow", "deny", "ask")



_PROVIDER_REGISTRY: dict[str, type[Provider]] = {}


class ProviderRegistryError(RuntimeError):
    """Raised when a provider name is already registered."""


def register_provider(name: str, cls: type[Provider]) -> None:
    """Register a provider class under ``name`` (dup-guarded)."""
    if name in _PROVIDER_REGISTRY:
        raise ProviderRegistryError(f"provider already registered: {name!r}")
    _PROVIDER_REGISTRY[name] = cls


def _load_provider_entry_points() -> None:
    """Discover + register out-of-tree providers via entry points (IoC).

    A built-in already self-registered wins (silent skip); a broken or duplicate
    third-party entry point is warned and skipped. First-party entry points
    shipped by ai-hats itself must fail loudly on load failure (HATS-1121).
    """
    try:
        entry_points = list(_provider_entry_points())
    except Exception as exc:  # noqa: BLE001 - discovery must never break import
        logger.warning("provider entry-point discovery failed: %s", exc)
        return
    for ep in entry_points:
        if ep.name in _PROVIDER_REGISTRY:
            continue
        try:
            cls = ep.load()
            register_provider(ep.name, cls)
        except Exception as exc:  # noqa: BLE001 - one bad plugin must not break the rest
            if _is_first_party_entry_point(ep):
                raise
            logger.warning("skipping provider entry point %r: %s", ep.name, exc)


_ENTRY_POINTS_LOADED = False


def _ensure_entry_points_loaded() -> None:
    global _ENTRY_POINTS_LOADED
    if not _ENTRY_POINTS_LOADED:
        _ENTRY_POINTS_LOADED = True
        _register_builtins()
        _load_provider_entry_points()


def provider_names() -> list[str]:
    """Registered provider names in registration order (deterministic)."""
    _ensure_entry_points_loaded()
    return list(_PROVIDER_REGISTRY)


class UnknownProviderError(ValueError):
    """Unknown provider name at ``get_provider``. Subclasses ``ValueError`` so
    existing ``except ValueError`` catchers keep working; carries ``name`` +
    ``available`` for the friendly CLI launch handler (mirrors
    ``RoleNotFoundError`` — HATS-965)."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(f"Unknown provider: {name}. Available: {available}")


def get_provider(name: str) -> Provider:
    """Get a provider instance by name."""
    _ensure_entry_points_loaded()
    cls = _PROVIDER_REGISTRY.get(name)
    if cls is None:
        raise UnknownProviderError(name, provider_names())
    return cls()


def _register_builtins() -> None:
    from ai_hats.surfaces.claude.provider import ClaudeProvider
    for name, cls in ((PROVIDER_CLAUDE, ClaudeProvider),):
        if name in _PROVIDER_REGISTRY:
            continue
        register_provider(name, cls)


def _reset_for_tests() -> None:
    """Clear the registry. Tests snapshot/restore around this."""
    _PROVIDER_REGISTRY.clear()
    global _ENTRY_POINTS_LOADED
    _ENTRY_POINTS_LOADED = False

