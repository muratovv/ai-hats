"""The contract every surface answers — ADR-0026 D14, HATS-1826.

A surface is one way of running an agent: Claude Code, agy, cline, codex,
opencode. This module says what all of them have in common, and nothing about
which ones exist — the catalog, the registry and the lookup are the
application's (``ai_hats.surface_registry``), because knowing the list is knowing how
the product uses the area.

Signatures and defaults, not the work behind them: what a default *does* when it
is more than a couple of lines lives beside this module (``system_prompt``,
``managed_tags``), so implementing a surface does not mean inheriting how ai-hats
reads a rule's metadata or writes a marker block. Six members are abstract; the
24 public ones carrying a default are overridden only when a harness differs, and
``opencode`` needed six of them.
"""  # comment-length: allow — the reviewed contract has to say what it is and is not

from __future__ import annotations

import abc
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol


from ai_hats_core import CompositionResult
from ai_hats_observe.parsers.trace import TraceParser

from ai_hats.session_artifacts import (
    ArtifactCategory,
    AutomateLaunch,
    BuiltArtifacts,
    RunMode,
    SessionPolicy,
    assemble_meta_prompt,
)

from .system_prompt import compose_sections, write_managed_block

if TYPE_CHECKING:
    from ai_hats_observe.parsers.base import TranscriptParser

logger = logging.getLogger(__name__)


@dataclass
class SurfaceRunResult:
    exit_code: int
    session_id: str | None
    total_cost_usd: float
    num_turns: int
    stop_reason: str | None
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None


@dataclass
class SurfaceHint:
    """A CLI hint describing a parameter or state supported by the provider."""

    name: str
    values: str
    description: str


class TranscriptResolver(Protocol):
    """``Surface.resolve_transcript`` seen from outside — where a session's log landed.

    Named here because the application passes it down as a value rather than importing
    a surface to find a file (HATS-1087): it rides ``CompositionPayload`` to the runners
    and ``AuditParams`` to the audit step. Until HATS-1826 it had two spellings and no
    owner — ``debt.TranscriptResolver = object`` and a bare ``Callable`` on the payload —
    which is the shape ``debt.py`` exists to prevent.
    """

    def __call__(
        self,
        project_dir: Path,
        session_id: str,
        *,
        provider_session_id: str | None = None,
        end_ts: float | None = None,
    ) -> list[Path]: ...


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
        artifacts: BuiltArtifacts | None = None,
    ) -> SurfaceRunResult:
        pass


class Surface(abc.ABC):
    """Abstract provider interface."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    def detected_home_dirs(self) -> list[str]:
        """Directory names under $HOME to check for provider presence (e.g. ['.agy', '.gemini'])."""
        return [f".{self.name}"]

    def surface_hints(self) -> list[SurfaceHint]:
        """A list of hints for the user about supported parameters and states.

        Returned by CLI (e.g. `ai-hats --help`) when this provider is active.
        """
        return []

    @abc.abstractmethod
    def system_prompt_path(self, project_dir: Path) -> Path | None:
        """Path to the system prompt file for this provider, or None if omitted."""

    @abc.abstractmethod
    def rules_dir(self, session_dir: Path) -> Path:
        """Directory where rules files should be placed."""

    def session_skills_root(self, project_dir: Path, session_id: str) -> Path | None:
        """Where this surface mirrors the session's composed skills (HATS-1540).

        The root a bound check resolves its script from in-session, one level
        above the per-skill directory. ``None`` means this surface mirrors no
        skills, so a binding has nothing to resolve against and refuses —
        ``legacy_launch_notices`` announces that at launch rather than leaving it
        to be discovered when a gate does not fire. Concrete, not abstract: an
        out-of-tree provider behind ``ai_hats.surface_registry`` predates this accessor
        and must keep importing (ADR-0019 D9).
        """  # comment-length: allow — the None branch IS the contract
        return None

    @contextlib.contextmanager
    def execution_context(self, project_dir: Path) -> contextlib.AbstractContextManager[None]:
        """Context manager active around provider CLI execution.

        Subclasses override to perform workspace setup/teardown during launch.
        """
        yield

    @abc.abstractmethod
    def build_system_prompt(self, result: CompositionResult) -> str:
        """Build the complete system prompt from composition result."""

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
        """Materialize one category of session artifacts for this surface (ADR-0018).

        Dispatches to a ``_build_<category>_<run_mode>`` method so a surface never
        branches on the run mode: HITL delivery (launch flags) and AUTOMATE
        delivery (inline / SDK) are different jobs that happen to share a name.
        A combination a surface does not deliver is simply an absent method —
        visible, unlike an ``else`` that falls through in silence.
        """
        handler = getattr(self, f"_build_{category.value}_{RunMode(run_mode).value}", None)
        if handler is None:
            logger.debug("Provider %s delivers no %s in %s", self.name, category, run_mode)
            return
        handler(project_dir, result, session_id, artifacts)

    def handles_artifact_categories(self) -> bool:
        """Whether this surface implements the ADR-0018 per-category seam.

        False for a pre-ADR-0018 out-of-tree provider that overrides only
        ``build_session_prompt``: routing it through the builder would deliver an
        empty session rather than fail, since the dispatch above finds no handler.
        """
        if type(self).build_category_artifact is not Surface.build_category_artifact:
            return True  # overrides the seam wholesale — its own dispatch
        return any(
            hasattr(self, f"_build_{c.value}_{m.value}") for c in ArtifactCategory for m in RunMode
        )

    def build_session_artifacts(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
        *,
        run_mode: RunMode | str = RunMode.HITL,
        policy: SessionPolicy | None = None,
        artifacts: BuiltArtifacts,
    ) -> BuiltArtifacts:
        """Build and materialize session artifacts per category and provider delivery mode.

        The caller owns ``artifacts`` and therefore its ``port``: hand one carrying
        a ``PlanMaterializer`` and the whole build becomes a dry-run (HATS-1211).
        """
        mode = RunMode(run_mode)
        policy = policy or SessionPolicy()
        artifacts.policy = policy
        # HATS-1540: no second copy here. The SKILLS category already writes an
        # unconditional mirror of every composed skill (SessionPolicy has no
        # skills field), and `session_skills_root` is what a check resolves off.
        for category in ArtifactCategory:
            if policy.is_enabled(category):
                self.build_category_artifact(
                    category,
                    project_dir,
                    result,
                    session_id,
                    run_mode=mode,
                    artifacts=artifacts,
                )
        return artifacts

    def transcript_parser(self) -> TranscriptParser:
        """The parser ``AuditWriter`` uses for this surface's session record.

        HATS-948: the parser rides the provider (no separate registry). Default
        is trace-only; a surface with a structured session log (Claude JSONL)
        overrides with a richer parser.
        """
        return TraceParser()

    def resolve_transcript(
        self,
        project_dir: Path,
        session_id: str,
        *,
        provider_session_id: str | None = None,
        end_ts: float | None = None,
    ) -> list[Path]:
        """Resolve the path(s) to this surface's structured session transcript(s).

        HATS-1087 / HATS-1400: ``transcript_parser`` knows HOW to parse; this knows
        WHERE to find the file(s). Default [] — no structured transcript → the
        trace-log fallback (TraceParser on ``session.trace_path``). A surface
        with a structured session log (Claude JSONL, agy brain segments, cline ``.messages.json``)
        overrides to discover them.
        """
        del project_dir, session_id, provider_session_id, end_ts
        return []

    def leaked_user_global_project_hooks(self, home: Path) -> list[str]:
        """ai-hats project-hook commands this surface leaked into user-global config.

        ai-hats wires hooks only into *project* config; a copy in user-global
        config double-fires and 404s off project-root (HATS-961). Base surfaces
        manage no user-global hooks → none; ClaudeSurface overrides to scan
        ``~/.claude/settings.json``.
        """
        return []

    def settings_lint_warnings(self, project_dir: Path) -> list[str]:
        """Known surface-settings pitfalls to surface at session start (HATS-1006).

        Base surfaces lint nothing; ClaudeSurface overrides to check the Claude
        settings chain for permission rules the CLI has deprecated.
        """
        return []

    def _compose_sections(self, result: CompositionResult) -> str:
        """The shared system-prompt sections — see ``system_prompt.compose_sections``.

        Kept as a method because it is the seam every surface calls on ``self``, in
        this tree and out of it.
        """
        return compose_sections(result)

    @abc.abstractmethod
    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        """Get the CLI command to launch this provider."""

    def get_cli_launch_args(
        self, base_cmd: list[str], session_id: str, is_resume: bool
    ) -> list[str]:
        """Surface-specific launch flags (e.g. session-id linkage).

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

    def supports_session_command_wrappers(self) -> bool:
        """Whether HITL children inherit an authoritative session PATH."""
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

    def describe_automate_launch(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
        artifacts: BuiltArtifacts,
        *,
        task: str,
        ticket_id: str,
        model: str,
        env: dict[str, str],
    ) -> AutomateLaunch:
        """The sub-agent launch this surface performs — argv and prompt together.

        One expression for ``SubAgentRunner`` and for ``--dry-run``: a report
        assembled by a second function is a report about a different launch
        (HATS-1552). Default covers every CLI surface; an SDK surface overrides.
        """
        del result, session_id, env
        prompt = assemble_meta_prompt(
            project_dir,
            role_context=artifacts.full_content or "",
            task=task,
            ticket_id=ticket_id,
        )
        flags = self.model_flags(model) if model else []
        cmd = self.get_cli_command() + artifacts.cli_args + flags
        return AutomateLaunch(launch=self.get_run_command(cmd, prompt), prompt=prompt)

    @abc.abstractmethod
    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        """Environment variables this provider needs. Pure — claims nothing.

        A value that only exists once something is taken for real (a bound port,
        a lease) belongs in :meth:`claim_launch_env`, or ``--dry-run`` performs
        the side effect while reporting a value the launch will not use.
        """

    def claim_launch_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        """Env values a launch must claim for real — ``{}`` for most surfaces.

        Called only on the launch path. Keys must be a subset of
        :meth:`get_env`'s, so a report names them either way (HATS-1554).
        """
        del session_dir, project_dir
        return {}

    def build_session_prompt(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        """Build CLI args and env vars for a per-session composed prompt.

        Called for EVERY session (default role and explicit ``--role`` alike).
        ``session_id`` keys the per-session cache dir
        ``<cache_root>/sessions/<session_id>/`` — outside the project — where
        the provider writes the prompt file and plugin-dir. Caller owns dir
        cleanup at session_end (``_cleanup_session_cache`` in runtime.py).

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

    def ensure_runtime_hooks(
        self, project_dir: Path, result: CompositionResult | None = None, **kwargs
    ) -> None:
        """Install provider-specific runtime hooks (e.g. Claude Code PreToolUse).

        Called by ``Assembler._refresh`` after the provider scaffold is
        ensured. Idempotent — safe to invoke on every role apply.

        ``result`` is the active role's composition (``None`` on the legacy
        bare-bump path with no active role); ``ClaudeSurface`` reads the
        skills' ``runtime_hooks:`` declarations from it (HATS-597).

        Default: no-op. Providers without a runtime-hook channel (Agy)
        rely on the rule layer plus skill-contributed git hooks.

        HATS-437: ClaudeSurface overrides to write a PreToolUse entry
        for ``library/hooks/pre_bash_shared_state_guard.sh`` into
        ``.claude/settings.json``, plus any skill-declared runtime hooks.
        """
        del project_dir, result
        return None

    def runtime_wiring_changes(
        self, project_dir: Path, result: CompositionResult | None = None
    ) -> list[tuple[str, str]]:
        """Managed runtime-hook wiring drift as ``[(name, "wiring")]``. Default:
        none (no settings.json channel); ``ClaudeSurface`` overrides (HATS-833)."""
        del project_dir, result
        return []

    def update_system_prompt(self, project_dir: Path, content: str) -> Path | None:
        """Write or update the inline system prompt block.

        Used by providers without a scaffold (e.g. Agy) to maintain the
        AI-HATS-managed section of `./GEMINI.md` between `INJECTION_START` /
        `INJECTION_END` markers. For providers that declare a scaffold
        (Claude — HATS-284), this method is dormant: `Assembler.set_role`
        skips the call entirely (HATS-286), and the lowercase-marker early
        return below provides a defense-in-depth no-op if it is invoked
        anyway.
        """
        prompt_path = self.system_prompt_path(project_dir)
        if prompt_path is None:
            return None
        return write_managed_block(prompt_path, content, project_dir=project_dir)
