"""The contract every surface answers — ADR-0026 D14.

A surface is one way of running an agent. This module says what all of them have in
common and names none of them: which surfaces exist is the application's question,
answered by ``ai_hats.surface_registry`` off the entry-point group.

Signatures and defaults, not the work behind them: what a default *does* when it
is more than a couple of lines lives beside this module (``system_prompt``,
``managed_tags``), so implementing a surface does not mean inheriting how ai-hats
reads a rule's metadata or writes a marker block. The abstract members are what
no default can answer for a harness — its name, its command, its planner; the
defaulted ones are overridden only where a harness differs.
"""  # comment-length: allow — the reviewed contract has to say what it is and is not

from __future__ import annotations

import abc
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import TYPE_CHECKING, Callable, Mapping, Protocol


from ai_hats_core import CompositionResult
from ai_hats_observe.parsers.trace import TraceParser

from ai_hats.session_artifacts import RunMode, SessionPolicy, working_directory_section

from ..debt import SessionId
from .system_prompt import compose_sections, write_managed_block
from .mcp import StdioMCPServer
from .hook_channel import HookRow

if TYPE_CHECKING:
    from ai_hats_observe.canonical.reader import EventReader
    from ai_hats_observe.event_log_writer import EventSource
    from ai_hats_observe.parsers.base import TranscriptParser

    from ai_hats.session_run import SessionRun

    from .plan import CompositionPlan, Digested, Host, Launched, LaunchFlags, MaterializationPlan

logger = logging.getLogger(__name__)


@dataclass
class SurfaceRunResult:
    """How the sub-agent process ended — what the caller branches on.

    What the run *cost* is not here: that goes to the ``MetricsSink`` handed to
    ``SubagentEngine.run``. This used to carry ``total_cost_usd``,
    ``num_turns``, ``stop_reason`` and the surface's session id as typed fields,
    which put ai-hats' metrics schema inside the contract every surface implements
    — adding one metric meant editing all of them.
    """

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None


class MetricsSink(Protocol):
    """Where a surface reports what its run cost; ai-hats decides where that lands.

    The surface names its own keys and never learns the on-disk layout.
    """

    def record(self, values: Mapping[str, object]) -> None: ...


@dataclass
class SurfaceHint:
    """A CLI hint describing a parameter or state supported by the surface."""

    name: str
    values: str
    description: str


class TranscriptResolver(Protocol):
    """``Surface.resolve_transcript`` seen from outside — where a session's log landed.

    Named here because the application passes it down as a value rather than importing
    a surface to find a file: it rides ``CompositionPayload`` to the runners
    and ``AuditParams`` to the audit step. It used to have two spellings and no
    owner — ``debt.TranscriptResolver = object`` and a bare ``Callable`` on the payload —
    which is the shape ``debt.py`` exists to prevent.
    """

    def __call__(
        self,
        # Where the surface RAN — `layout.cwd`: a worktree, a subdirectory. The
        # surface keys its record by it; the project root is where it never was.
        cwd: Path,
        # ai-hats' own session id, `YYYYMMDD-HHMMSS-<n>-<pid>`. Its first 15 chars
        # ARE the session's start time and the resolver parses them, so this is a
        # timestamped identity, never an opaque uuid (`paths.session_start_ts`).
        session_id: SessionId,
        *,
        # The same session in the SURFACE's numbering (Claude's uuid4). Not a parent
        # session: held, it names the transcript exactly instead of guessing by time.
        provider_session_id: str | None = None,
        # Upper bound of the mtime window when there is no id to match on — without
        # it a file written after the session ended matches.
        end_ts: float | None = None,
    ) -> list[Path]: ...


class SubagentEngine(abc.ABC):
    """Abstract engine for executing a subagent."""

    @abc.abstractmethod
    def run(
        self,
        *,
        layout: ProjectLayout,
        work_dir: Path,
        session_id: SessionId,
        env: dict[str, str],
        model: str | None,
        timeout_s: int,
        metrics: MetricsSink,
        # The pair the plan was launched into (ADR-0036 D4): the option
        # document for this run, and the sub-agent's first turn.
        launched: Launched,
        brief: str | None,
        # The id the runner minted for the surface's own record, so the record
        # can be followed while the run is on; an engine that cannot pass one
        # on ignores it and reports the id the surface chose in ``metrics``.
        provider_session_id: str | None = None,
        # The session's live log, for what only the surface's own stream
        # carries (a quota pre-warning); ``None`` when the session writes none.
        event_log: Path | None = None,
    ) -> SurfaceRunResult:
        pass


class Surface(abc.ABC):
    """What every surface answers: a few members abstract, the rest defaulted."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    def detected_home_dirs(self) -> list[str]:
        """Directory names under $HOME to check for the surface's presence (e.g. ['.agy', '.gemini'])."""
        return [f".{self.name}"]

    def surface_hints(self) -> list[SurfaceHint]:
        """A list of hints for the user about supported parameters and states.

        Returned by CLI (e.g. `ai-hats --help`) when this surface is active.
        """
        return []

    @abc.abstractmethod
    def system_prompt_path(self, layout: ProjectLayout) -> Path | None:
        """Path to the system prompt file for this surface, or None if omitted."""

    @abc.abstractmethod
    def rules_dir(self, session_dir: Path) -> Path:
        """Directory where rules files should be placed."""

    def session_skills_root(self, layout: ProjectLayout, session_id: SessionId) -> Path | None:
        """Where this surface mirrors the session's composed skills.

        The root a bound check resolves its script from in-session, one level
        above the per-skill directory. ``None`` means this surface mirrors no
        skills, so a binding has nothing to resolve against and refuses — the
        record's ``checks`` rows say so at launch rather than leaving it to be
        discovered when a gate does not fire. Concrete, not abstract: an
        out-of-tree surface behind ``ai_hats.surface_registry`` predates this accessor
        and must keep importing (ADR-0019 D9).
        """  # comment-length: allow — the None branch IS the contract
        return None

    @contextlib.contextmanager
    def execution_context(self, layout: ProjectLayout) -> contextlib.AbstractContextManager[None]:
        """Context manager active around the surface's CLI execution.

        Subclasses override to perform workspace setup/teardown during launch.
        """
        yield

    @abc.abstractmethod
    def build_system_prompt(self, result: CompositionResult) -> str:
        """Build the complete system prompt from composition result."""

    def probe_home(self, environ: Mapping[str, str]) -> Digested | None:
        """The person's configuration home as this surface projects it into a
        session, enumerated before planning and handed in as ``Host.home``
        (ADR-0036 D2) — the one read of the home a plan is built on. ``None``:
        this surface projects no home.
        """
        del environ
        return None

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
        """This surface's entries, environment and launch for one session root
        (ADR-0036 D2): a function of its inputs that reads no disk.

        No default: every road into a session passes ``plan_session``, which
        refuses a surface that has not written its planner.
        """
        raise NotImplementedError(f"{self.name} does not plan a session")

    def automate_launch(
        self,
        plan: MaterializationPlan,
        flags: LaunchFlags,
        env: Mapping[str, str],
        *,
        layout: ProjectLayout,
    ) -> Launched:
        """The sub-agent launch of a CLI surface (ADR-0036 D4): the plan's argv
        plus the model, and one prompt token — context, working directory, brief.
        An SDK surface overrides with its option document.
        """
        from .plan import Launched, context_text

        prompt = "\n\n".join(
            s
            for s in (context_text(plan), working_directory_section(layout), flags.brief or "")
            if s
        )
        model = self.model_flags(flags.model) if flags.model else []
        cmd = self.get_cli_command() + list(plan.launch.args or ()) + model
        return Launched(
            args=tuple(self.get_run_command(cmd, prompt)), sdk_options=None, env=env, prompt=prompt
        )

    def describe_launch(self, launched: Launched) -> list[str]:
        """How a record names the launch: the argv; an SDK surface says ``k=v``."""
        return list(launched.args or ())

    def transcript_parser(self) -> TranscriptParser:
        """The parser ``AuditWriter`` uses for this surface's session record.

        The parser rides the surface (no separate registry). Default
        is trace-only; a surface with a structured session log (Claude JSONL)
        overrides with a richer parser.
        """
        return TraceParser()

    def event_reader(self) -> Callable[[Path], EventReader] | None:
        """Builds the reader that follows ONE of this surface's transcripts as
        canonical events while the session runs — same rule as
        ``transcript_parser``: it rides the surface, there is no registry. The
        reader must hold its tail open until ``close()``; the session's writer
        is what declares the run over.

        Default ``None``, not a reader that yields nothing: ``None`` means this
        surface has no canonical reading yet, so the session writes no
        ``events.jsonl`` at all — while an empty artifact would claim a session
        that emitted nothing. Claude is the only surface that overrides today;
        agy, cline, codex and opencode keep the default.
        """  # comment-length: allow — the None branch IS the contract
        return None

    def event_sources(
        self,
        cwd: Path,
        session_id: SessionId,
        *,
        provider_session_id: str | None = None,
    ) -> "list[EventSource]":
        """Every record the session's live writer follows, and whose work each
        holds — the main agent's (``agent=None``) and each sub-agent's.

        Default: what ``resolve_transcript`` names, as the main agent's. A surface
        that files a sub-agent's record beside the parent's overrides to name
        those too; ``resolve_transcript`` stays the post-hoc audit's input and
        names the main record alone.
        """
        from ai_hats_observe.event_log_writer import EventSource

        return [
            EventSource(path)
            for path in self.resolve_transcript(
                cwd, session_id, provider_session_id=provider_session_id
            )
        ]

    def resolve_transcript(
        self,
        cwd: Path,
        session_id: SessionId,
        *,
        provider_session_id: str | None = None,
        end_ts: float | None = None,
    ) -> list[Path]:
        """Resolve the path(s) to this surface's structured session transcript(s).

        ``transcript_parser`` knows HOW to parse; this knows
        WHERE to find the file(s). ``cwd`` is where the surface ran — the key a
        surface such as Claude files its record under. Default [] — no
        structured transcript → the trace-log fallback (TraceParser on
        ``session.trace_path``). A surface with a structured session log (Claude
        JSONL, agy brain segments, cline ``.messages.json``) overrides to discover them.
        """
        del cwd, session_id, provider_session_id, end_ts
        return []

    def leaked_user_global_project_hooks(self, home: Path) -> list[str]:
        """ai-hats project-hook commands this surface leaked into user-global config.

        ai-hats wires hooks only into *project* config; a copy in user-global
        config double-fires and 404s off project-root. Base surfaces
        manage no user-global hooks → none; ClaudeSurface overrides to scan
        ``~/.claude/settings.json``.
        """
        return []

    def settings_lint_warnings(self, layout: ProjectLayout) -> list[str]:
        """Known surface-settings pitfalls to surface at session start.

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
        """Get the CLI command to launch this surface."""

    def get_cli_launch_args(
        self, base_cmd: list[str], session_id: SessionId, is_resume: bool
    ) -> list[str]:
        """Surface-specific launch flags (e.g. session-id linkage).

        Default: none. ``wrap_runner`` calls this on EVERY surface, so a
        claude-only override left agy/cline/gemini raising AttributeError
        before launch.
        """
        return base_cmd

    def model_flags(self, model: str) -> list[str]:
        """Convert a model name into surface-specific CLI flags."""
        return ["--model", model]

    def mcp_form_cli_args(self, server: StdioMCPServer) -> list[str] | None:
        """Return form-server launch arguments, or None when this surface cannot deliver forms."""
        return None

    def command_guard_rows(self, environ: Mapping[str, str]) -> list[HookRow]:
        """Load and validate this session's command guards for an external integration."""
        raise NotImplementedError(f"{self.name} does not expose session command guards")

    def supports_session_command_wrappers(self) -> bool:
        """Whether HITL children inherit an authoritative session PATH."""
        return False

    def engine(self) -> SubagentEngine | None:
        """Get the native SDK SubagentEngine for this surface."""
        return None

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
    ) -> list[str]:
        """Build a non-interactive command that runs ``meta_prompt`` through this surface.

        Default: return ``cmd`` unchanged. Subclasses tailor the invocation
        to their CLI (e.g. Claude needs ``--print -p``, Agy needs ``-p``).
        """
        return cmd

    @abc.abstractmethod
    def get_env(self, session_dir: Path, layout: ProjectLayout) -> dict[str, str]:
        """Environment variables this surface needs. Pure — claims nothing.

        A value that only exists once something is taken for real (a bound port,
        a lease) belongs in :meth:`claim_launch_env`, or ``--dry-run`` performs
        the side effect while reporting a value the launch will not use.
        """

    def serve_hooks(self, layout: ProjectLayout, session_id: str, environ: dict[str, str]):
        """A dispatcher held open for the whole session, or ``None`` for a
        surface that spawns one per hook — which every surface still does when
        this returns ``None`` or the one it returns cannot be reached.

        The object must carry ``path`` and ``close()``; the runner logs the
        first and calls the second.
        """
        del layout, session_id, environ
        return None

    def claim_launch_env(self, session_dir: Path, layout: ProjectLayout) -> dict[str, str]:
        """Env values a launch must claim for real — ``{}`` for most surfaces.

        Called only on the launch path. Keys must be a subset of
        :meth:`get_env`'s, so a report names them either way.
        """
        del session_dir, layout
        return {}

    def claim_resources(
        self,
        plan: MaterializationPlan,
        flags: LaunchFlags,
        *,
        layout: ProjectLayout,
        run: SessionRun,
    ) -> None:
        """What a real session takes beyond the plan and gives back at its end:
        finalizers registered on ``run``, warnings about what was recovered on
        the way in. The runners call it once the plan is applied; a preview
        never does. Default: nothing to take.
        """
        del plan, flags, layout, run

    def update_system_prompt(self, layout: ProjectLayout, content: str) -> Path | None:
        """Write or update the inline system prompt block.

        Used by surfaces without a scaffold (e.g. Agy) to maintain the
        AI-HATS-managed section of `./GEMINI.md` between `INJECTION_START` /
        `INJECTION_END` markers. For surfaces that declare a scaffold
        (Claude), this method is dormant: `Assembler.set_role`
        skips the call entirely, and the lowercase-marker early
        return below provides a defense-in-depth no-op if it is invoked
        anyway.
        """
        project_dir = layout.root
        prompt_path = self.system_prompt_path(layout)
        if prompt_path is None:
            return None
        return write_managed_block(prompt_path, content, project_dir=project_dir)
