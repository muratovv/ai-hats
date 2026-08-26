"""How this product launches a session — the policy the pipeline area runs with.

The area chains sessions and knows the two kinds apart; everything here is what a
given session is *for*, which is ours to decide, not its (ADR-0026 D14). Each class
implements ``pipeline.RunParams``: it answers where the run happens and spells the
funnel keys its steps declare — the same literals those steps list in their ``StepIO``,
which is why this lives beside them rather than inside the area.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from ai_hats_wt import IsolationMode

from .config import Channel
from .debt import (
    AuditWriterFactory,
    CompositionPayload,
    SessionFactory,
    SessionManager,
    StaticCostAnalyzer,
    TracerFactory,
)
from .pipeline import PipelineResult, PromptWriter, SessionRef
from .surfaces import TranscriptResolver


@dataclass(frozen=True)
class MaterializedRole:
    """The composed role a session acts as."""

    # Names the role for the steps that label the session and pick its prompt.
    name: str
    # Handed to the runner by the launch step; nothing on the way looks inside.
    composition: CompositionPayload


@dataclass(frozen=True)
class SessionRecording:
    """The sinks a session is written into."""

    # Creates the session directory and its metrics file.
    manager: SessionManager
    # Builds the sidecar tracer that captures the transcript.
    tracer_factory: TracerFactory


@dataclass(frozen=True)
class HarnessParams:
    """How the harness is launched. The branch is the type, never a flag."""

    # First user-visible message. HITL prepends it to the provider's argv; Automate
    # passes it as the sub-agent's task.
    prompt: str | None = None


@dataclass(frozen=True)
class Hitl(HarnessParams):
    """Human-in-the-loop: the provider CLI takes over the terminal."""

    # Forwarded to the provider's argv. HITL-only: the Automate branch of
    # ``steps/launch.py`` drops them, so the field must not be expressible there.
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class Automate(HarnessParams):
    """Non-interactive: a captured subprocess, reported when it exits.

    The three fields below are read by the Automate branch of ``steps/launch.py``
    and by nothing else: ``WrapRunner.run`` takes none of them, and the CLI already
    refuses all three interactively (``_BATCH_ONLY_FLAGS``).
    """

    # Model override for the sub-agent process.
    model: str = ""
    # How the sub-agent's checkout is disposed of when it finishes.
    isolation: IsolationMode = IsolationMode.DISCARD
    # Backlog card whose sections the provider renders into the sub-agent's first
    # message. TODO(HATS-1786): hand over rendered context instead of an id, so
    # launching a session does not reach into the backlog.
    ticket_id: str = ""


@dataclass(frozen=True)
class RoleAudit:
    """The role a ``reflect-role`` session inspects, and where it reads it from.

    role-judge reads the audited composition off disk with its own tools instead of
    through the funnel, so the breakdown is written into the run's scratch space and
    the first message is built from where it landed — which is why this launch alone
    needs the run to exist before its prompt does.
    """

    # The audited role's name, read by ``first_message`` below and nowhere else:
    # role-judge writes its own report (its L0 carve-out), so reflect-role ships no
    # ``save_artifact`` whose template could name it, and it is not a funnel key.
    target: str
    # Writes the breakdown under the scratch dir it is handed, and answers with the
    # directory the message points role-judge at.
    materialize: Callable[[Path], Path]
    # The first message, still holding its placeholders; the file it is written into
    # is what ``resolve_prompt`` (steps/prompt.py) reads.
    message_template: str

    def first_message(self, scratch_dir: Path, project_dir: Path) -> str:
        """Stage the breakdown, then fill the template with where it landed."""
        return self.message_template.format(
            target_role=self.target,
            composed_dir=self.materialize(scratch_dir),
            project_dir=project_dir,
        )


@dataclass(frozen=True)
class SessionRunParams:
    """Launch a composed role in a provider session."""

    # The project this run belongs to: every path a step touches hangs off it, and it
    # is resolved once at the entry point so nothing below rediscovers it (ADR-0026 D2).
    project_dir: Path
    role: MaterializedRole
    recording: SessionRecording
    harness: HarnessParams = field(default_factory=Automate)
    # Open k=v map stamped onto the session record. Deliberately not a fixed field
    # list: the CLI puts user ``--tag``s here and the retry path adds its own attempt
    # counter, so writers extend it without this contract changing.
    annotations: Mapping[str, str] | None = None
    # Set on ``reflect-role`` only, where it supplies the first message in place of
    # ``harness.prompt``: that message can only be written once the run's scratch
    # directory exists.
    audit: RoleAudit | None = None

    def to_state(self, *, materialize_prompt: PromptWriter, scratch_dir: Path) -> dict[str, Any]:
        harness = self.harness
        prompt = (
            harness.prompt
            if self.audit is None
            else self.audit.first_message(scratch_dir, self.project_dir)
        )
        state: dict[str, Any] = {
            "role": self.role.name,
            "composition": self.role.composition,
            "project_dir": self.project_dir,
            "session_mgr": self.recording.manager,
            "tracer_factory": self.recording.tracer_factory,
            "tags": dict(self.annotations) if self.annotations else None,
            "interactive": isinstance(harness, Hitl),
            "prompt_path": materialize_prompt(prompt),
        }
        if isinstance(harness, Hitl):
            state["extra_args"] = list(harness.extra_args)
        if isinstance(harness, Automate):
            state["model"] = harness.model
            state["isolation"] = harness.isolation.value
            state["ticket"] = harness.ticket_id
        return state


@dataclass(frozen=True)
class FinalizeRunParams:
    """Finish a session that already ran — the ``finalize-*`` sub-pipelines.

    Implements ``pipeline.SubpipelineParams``: no first message and nothing staged on
    disk, because the session whose directory these steps write into is over by the
    time this runs. The four handles below are the same explicit-dependency seam
    ``CompositionPayload`` carries for a launch (ADR-0026 D14) — the steps get given
    what they need, so no step imports observe.
    """

    # The finished session: ``make_audit`` reopens it by id and directory, and reads
    # the provider's transcript by the provider-side id.
    session: SessionRef
    # Where the session ran. ``make_audit`` hands it to ``transcript_resolver``, which
    # is how a provider finds its own transcripts (ADR-0026 D2).
    project_dir: Path
    # What the provider exited with. ``run_session_end`` reports it; ``make_audit``
    # takes it as a required key and reads metrics.json instead.
    exit_code: int
    # Reopens the session for ``make_audit``.
    session_factory: SessionFactory | None = None
    # Builds the writer ``make_audit`` rewrites ``audit.md`` through.
    audit_writer_factory: AuditWriterFactory | None = None
    # Locates the provider's transcript for ``make_audit``; absent, it degrades to
    # the trace log (HATS-1087).
    transcript_resolver: TranscriptResolver | None = None
    # Lets ``compute_usage`` cross-check cost when the provider reports none.
    static_cost_analyzer: StaticCostAnalyzer | None = None

    def to_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "session_id": self.session.id,
            "session_dir": self.session.dir,
            # ``make_audit`` requires the key and the funnel drops ``None``, so an
            # absent provider session id is the empty string — which is what both
            # callers already spelled before this contract existed.
            "claude_session_id": self.session.provider_session_id or "",
            "project_dir": self.project_dir,
            "exit_code": self.exit_code,
        }
        # None-filtered on the way in as well, so a handle nobody supplied never
        # reaches a step's ``optional`` as a present-but-empty value.
        for key, value in (
            ("session_factory", self.session_factory),
            ("audit_writer_factory", self.audit_writer_factory),
            ("transcript_resolver", self.transcript_resolver),
            ("static_cost_analyzer", self.static_cost_analyzer),
        ):
            if value is not None:
                state[key] = value
        return state


@dataclass(frozen=True)
class SessionOutcome:
    """What a session run gives its caller back — and the only reader of the code."""

    # Written by the launch step (steps/launch.py) under ``exit_code``; None when no
    # step reported one — the run started no session, or the step that starts it failed
    # under ``failure_policy=continue``. Callers name their own default below.
    exit_code: int | None = None
    session_id: str | None = None
    session_dir: Path | None = None

    @classmethod
    def of(cls, result: PipelineResult) -> SessionOutcome:
        session = result.session
        code = result.produced.get("exit_code")
        return cls(
            exit_code=None if code is None else int(code),
            session_id=None if session is None else session.id,
            session_dir=None if session is None else session.dir,
        )

    def exit_code_or(self, default: int) -> int:
        return default if self.exit_code is None else self.exit_code

    def require_session(self) -> tuple[str, Path]:
        """Identity and location, or the loud failure ``final[…]`` used to raise."""
        if self.session_id is None or self.session_dir is None:
            raise KeyError("pipeline produced no session_id / session_dir")
        return self.session_id, self.session_dir


@dataclass(frozen=True)
class ReflectSessionRunParams:
    """Review one past session — the launch that composes no role of its own.

    ``run_session_review`` owns the session it starts, down to which role reviews and
    how it records, so nothing about launching one reaches the funnel here: this
    family names the session under review and stops.
    """

    # The project this run belongs to: every path a step touches hangs off it, and it
    # is resolved once at the entry point so nothing below rediscovers it (ADR-0026 D2).
    project_dir: Path
    # The past session to review, handed to SessionReviewRunner by
    # ``run_session_review`` (steps/session_review.py).
    session_id: str
    # How many times ``run_session_review`` re-runs a reviewer that came back
    # unusable. Its YAML already carries 1; the CLI flag overrides that per run.
    max_retries: int = 1

    def to_state(self, *, materialize_prompt: PromptWriter, scratch_dir: Path) -> dict[str, Any]:
        del materialize_prompt, scratch_dir  # no first message, nothing staged on disk
        return {
            "session_id": self.session_id,
            "project_dir": self.project_dir,
            "max_retries": self.max_retries,
        }


@dataclass(frozen=True)
class InitRunParams:
    """Bootstrap a project — the launch with no session in it.

    ``ai-hats self init`` writes the config and the scaffolding, then hands the
    process over to a wizard session it does not run itself. Every field is a CLI
    flag one of the three init steps (steps/init_steps.py) reads.
    """

    # The project being initialized: the three steps resolve every path they write
    # from it, and it is resolved once at the entry point (ADR-0026 D2).
    project_dir: Path
    # Which provider the project is configured for. ``select_provider`` treats None
    # as "ask, or fall back"; the other two steps then require what it decided.
    provider: str | None = None
    # Default role written into ai-hats.yaml by ``bootstrap_project``. Paired with a
    # provider it also tells ``select_provider`` and ``prepare_execute_session`` that
    # no wizard is wanted.
    role: str | None = None
    # Task-id prefix for the tracker: written by ``bootstrap_project``, echoed back
    # to the user by ``prepare_execute_session``.
    task_prefix: str | None = None
    # Where the framework directory lives; same two readers as ``task_prefix``.
    ai_hats_dir: str | None = None
    # An existing venv to adopt instead of the managed one; same two readers.
    venv_path: str | None = None
    # Leaves .gitignore alone: ``bootstrap_project`` acts on it, and
    # ``prepare_execute_session`` says so in the summary.
    no_manage_gitignore: bool = False
    # Refuses the wizard: ``select_provider`` falls back instead of prompting, and
    # ``prepare_execute_session`` prepares no handoff.
    no_wizard: bool = False
    # Where the harness itself is installed from. ``select_provider`` prompts for it
    # when absent and ``bootstrap_project`` writes it; the funnel carries the value,
    # which is what ``Assembler.init`` takes.
    channel: Channel | None = None
    # The editable checkout ``channel=local`` installs from — ``bootstrap_project``.
    harness_path: str | None = None

    def to_state(self, *, materialize_prompt: PromptWriter, scratch_dir: Path) -> dict[str, Any]:
        del materialize_prompt, scratch_dir  # no first message, nothing staged on disk
        return {
            "project_dir": self.project_dir,
            "provider": self.provider,
            "role": self.role,
            "task_prefix": self.task_prefix,
            "ai_hats_dir": self.ai_hats_dir,
            "venv_path": self.venv_path,
            "no_manage_gitignore": self.no_manage_gitignore,
            "no_wizard": self.no_wizard,
            "channel": None if self.channel is None else self.channel.value,
            "harness_path": self.harness_path,
        }


@dataclass(frozen=True)
class SessionReviewOutcome:
    """Where a ``reflect-session`` run left the review it wrote."""

    # Written by ``run_session_review`` (steps/session_review.py).
    review_path: Path | None = None

    @classmethod
    def of(cls, result: PipelineResult) -> SessionReviewOutcome:
        return cls(review_path=result.produced.get("review_path"))

    def require_review_path(self) -> Path:
        """The review, or the loud failure ``final[…]`` used to raise."""
        if self.review_path is None:
            raise KeyError("pipeline produced no review_path")
        return self.review_path


@dataclass(frozen=True)
class ReportOutcome:
    """Where a run that saves its report left it."""

    # Written by ``save_artifact`` (steps/save.py). None when the run got that far
    # without producing one — which is how the judge phases tell a usable draft from
    # a run that only looked successful.
    saved_path: Path | None = None

    @classmethod
    def of(cls, result: PipelineResult) -> ReportOutcome:
        return cls(saved_path=result.produced.get("saved_path"))


@dataclass(frozen=True)
class IntakeOutcome:
    """The intake decision a ``reflect-issue`` run pulled out of the transcript."""

    # Written by ``extract_marker`` (steps/extract.py) under the ``out_key`` the
    # pipeline names. Empty when the markers were missing — the caller reads that as
    # a failed run, not as an empty decision.
    text: str = ""

    @classmethod
    def of(cls, result: PipelineResult) -> IntakeOutcome:
        return cls(text=result.produced.get("intake_result") or "")


@dataclass(frozen=True)
class InitOutcome:
    """The handoff an ``init`` run prepared for its caller to exec."""

    # Built by ``prepare_execute_session`` (steps/init_steps.py). None when the run
    # decided against handing over — flags-only path, or no ai-hats on PATH.
    execute_cmd: list[str] | None = None

    @classmethod
    def of(cls, result: PipelineResult) -> InitOutcome:
        return cls(execute_cmd=result.produced.get("execute_cmd"))
