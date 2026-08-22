"""Public contract of the pipeline area: run a materialized pipeline config.

The area knows how to run step1 -> ... -> stepN and nothing about who runs what:
which pipelines exist is the application's catalog, not this module's enum
(ADR-0026 D14). Import-light on purpose — dataclasses and the funnel mapping only;
``run_pipeline`` pulls the harness inside its body (HATS-1783).

Every field below is here because a step reads it; the comment on each says which
one, so a field nobody reads is visible as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from ai_hats_wt import IsolationMode

from . import keys as _keys

# Writes prompt text into the run's scratch space and answers with its path, because
# the steps take a file rather than a string. Supplied by the harness, so a caller
# hands over text and never learns where it landed.
PromptWriter = Callable[[str | None], Path | None]

# Values the area carries to the steps and never looks inside. Aliased rather than
# spelled ``object`` so every undecided type is one grep, not a scatter.
# TODO(HATS-1785): real types once composition has a public contract — annotating
# them today would add pipeline -> composition / observe edges the ADR forbids.
CompositionPayload = object
SessionManager = object
TracerFactory = object


@dataclass(frozen=True)
class PipelineConfig:
    """Which pipeline to run — the materialized ``pipeline.yaml``."""

    # TODO(HATS-1783): grows a ``path`` when the run_yaml consumers migrate —
    # project-local pipelines are loaded by path, not by library name.
    name: str


@dataclass(frozen=True)
class MaterializedRole:
    """The composed role this run acts as."""

    # Names the role for the steps that label the session and pick its prompt.
    name: str
    # The composition payload the launch step hands to the runner.
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
    and by nothing else — which is why they sit here rather than on the session:
    ``WrapRunner.run`` takes none of them, and the CLI already refuses all three
    interactively (``_BATCH_ONLY_FLAGS``).
    """

    # Model override for the sub-agent process.
    model: str = ""
    # How the sub-agent's checkout is disposed of when it finishes.
    isolation: IsolationMode = IsolationMode.DISCARD
    # Backlog card whose sections are rendered into the sub-agent's first message
    # (``assemble_first_user_message`` -> ``ticket_sections``). Not the parent
    # session: that is a separate runner argument, and nothing seeds it today.
    ticket_id: str = ""


@dataclass(frozen=True)
class RunParams:
    """Everything about this run, and nothing about which pipeline it is."""

    # The project this run belongs to: every path a step touches hangs off it, and it
    # is resolved once at the entry point so nothing below rediscovers it (ADR-0026 D2).
    project_dir: Path
    role: MaterializedRole
    recording: SessionRecording
    harness: HarnessParams = field(default_factory=Automate)
    # Open k=v map stamped onto the session record. Deliberately not a fixed field
    # list: the CLI puts user ``--tag``s here and the retry path adds its own
    # attempt counter, so writers extend it without the contract changing.
    annotations: Mapping[str, str] | None = None


def _state_of(params: RunParams, materialize_prompt: PromptWriter) -> dict[str, Any]:
    """Funnel state for ``params`` — the seam between the typed contract and the steps.

    Internal on purpose: callers describe a run, and the vocabulary each step declares
    is not part of what they describe.
    """
    harness = params.harness
    state: dict[str, Any] = {
        _keys.KEY_ROLE: params.role.name,
        _keys.KEY_COMPOSITION: params.role.composition,
        _keys.KEY_PROJECT_DIR: params.project_dir,
        _keys.KEY_SESSION_MGR: params.recording.manager,
        _keys.KEY_TRACER_FACTORY: params.recording.tracer_factory,
        _keys.KEY_TAGS: dict(params.annotations) if params.annotations else None,
        _keys.KEY_INTERACTIVE: isinstance(harness, Hitl),
        _keys.KEY_PROMPT_PATH: materialize_prompt(harness.prompt),
    }
    if isinstance(harness, Hitl):
        state[_keys.KEY_EXTRA_ARGS] = list(harness.extra_args)
    if isinstance(harness, Automate):
        state[_keys.KEY_MODEL] = harness.model
        state[_keys.KEY_ISOLATION] = harness.isolation.value
        state[_keys.KEY_TICKET] = harness.ticket_id
    return state


@dataclass(frozen=True)
class SessionRef:
    """The session a run produced: its identity and where its artefacts landed."""

    id: str
    dir: Path
    provider_session_id: str | None = None


@dataclass(frozen=True)
class PipelineResult:
    """What the run produced and what went wrong on the way."""

    # None when no step reported one: a pipeline that launches nothing (init,
    # reflect-session), or a launch step that failed under ``failure_policy=continue``
    # and left the run going. Callers name their own default via ``exit_code_or``.
    exit_code: int | None = None
    session: SessionRef | None = None
    # Step name -> the exception a ``failure_policy=continue`` step swallowed,
    # as the runner records it (``pipeline.py``).
    errors: Mapping[str, BaseException] = field(default_factory=dict)

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> PipelineResult:
        code = state.get(_keys.KEY_EXIT_CODE)
        session_id = state.get(_keys.KEY_SESSION_ID)
        session_dir = state.get(_keys.KEY_SESSION_DIR)
        session = (
            SessionRef(
                id=session_id,
                dir=session_dir,
                provider_session_id=state.get(_keys.KEY_CLAUDE_SESSION_ID),
            )
            if session_id is not None and session_dir is not None
            else None
        )
        return cls(
            exit_code=None if code is None else int(code),
            session=session,
            errors=dict(state.get(_keys.KEY_ERRORS) or {}),
        )

    def exit_code_or(self, default: int) -> int:
        return default if self.exit_code is None else self.exit_code

    def require_session(self) -> SessionRef:
        """The session, or the loud failure ``final[KEY_SESSION_ID]`` used to raise."""
        if self.session is None:
            raise KeyError("pipeline produced no session_id / session_dir")
        return self.session


def run_pipeline(config: PipelineConfig, params: RunParams) -> PipelineResult:
    """Run the configured pipeline: user steps, per-session namespace, GC, tracing."""
    from .harness import PipelineHarness  # deferred: costs ~160 ms of import at startup

    with PipelineHarness(config.name, params.project_dir) as harness:
        state = _state_of(params, harness.materialize_prompt)
        return PipelineResult.from_state(harness.run(state))
