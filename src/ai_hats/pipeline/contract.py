"""Public contract of the pipeline area: run a materialized pipeline config.

The area chains sessions and threads state between the steps that run them. Two kinds
of session exist for it — one that takes over the terminal and one captured as a
subprocess — and that distinction is the primitive's own, because a chain mixes them.

What a session is *for* is not: its role, its model, the context it is handed and the
sinks it records into are the application's policy. They arrive inside a params object
the area never reads and hands to the steps as-is (ADR-0026 D14).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from . import keys as _keys

# Writes prompt text into the run's scratch space and answers with its path, because
# the steps take a file rather than a string. Supplied by the harness, so a caller
# hands over text and never learns where it landed.
PromptWriter = Callable[[str | None], Path | None]


@dataclass(frozen=True)
class PipelineConfig:
    """Which pipeline to run — the materialized ``pipeline.yaml``."""

    # TODO(HATS-1797): grows a ``path`` when `pipeline run` lands — a project-local
    # pipeline is named by path, and only ``harness.run_yaml`` can take one today.
    name: str


@runtime_checkable
class RunParams(Protocol):
    """What every pipeline family answers: where the run happens, and its state.

    Implemented outside the area, next to the steps that read the state: the funnel
    keys are each step's own declaration, and repeating them here would make the area
    the owner of a vocabulary it does not use.

    ``scratch_dir`` is the run's own directory, disposed of with it: a first message
    may name a path that has to exist before the run starts — the role audit in
    ``cli/reflect.py`` writes the audited composition there and points at it.
    """

    @property
    def project_dir(self) -> Path: ...

    def to_state(
        self,
        *,
        materialize_prompt: PromptWriter,
        scratch_dir: Path,
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class SubpipelineParams(Protocol):
    """The whole initial state of a run that happens inside an existing session.

    No ``materialize_prompt`` and no ``scratch_dir``, which is what separates it from
    ``RunParams``: a sub-pipeline is finalizing a session that already ran, so there is
    no first message to stage and no fresh directory to stage it in. Implemented
    outside the area for the same reason ``RunParams`` is — the keys are the steps' own
    declarations.
    """

    def to_state(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class SessionRef:
    """A session the run produced — the handle a later link of the chain refers to."""

    id: str
    dir: Path
    provider_session_id: str | None = None


# The funnel keys this contract answers for. They are lifted out of ``produced``
# rather than copied beside it, so each of them has exactly one spelling on the
# result — see the class docstring.
_TYPED_HERE = (
    _keys.KEY_SESSION_ID,
    _keys.KEY_SESSION_DIR,
    _keys.KEY_CLAUDE_SESSION_ID,
    _keys.KEY_ERRORS,
)


@dataclass(frozen=True)
class PipelineResult:
    """What the run produced and what went wrong on the way.

    Every value here is readable one way. The area answers for two of them and
    types them; the rest stay in ``produced`` under the name the step that wrote
    them declared, and the application reads those through the typed readers in
    ``session_policy.py``. ``produced`` is the funnel **minus** the two below, so
    a value with a field here has no second spelling to drift from.
    """

    # The session the run started, which is what a later link of a chain refers to:
    # the area chains sessions, so this is its own currency and not policy. None when
    # the run started none.
    session: SessionRef | None = None
    # Step name -> the exception a ``failure_policy=continue`` step swallowed,
    # as the runner records it (``pipeline.py``).
    errors: Mapping[str, BaseException] = field(default_factory=dict)
    # The funnel minus the two above, by the name each step declared — the area
    # carries the map and reads none of it. Its typed readers are in
    # ``session_policy.py``, the exit code among them: what a code means is policy.
    produced: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> PipelineResult:
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
            session=session,
            errors=dict(state.get(_keys.KEY_ERRORS) or {}),
            produced={key: value for key, value in state.items() if key not in _TYPED_HERE},
        )


def run_pipeline(config: PipelineConfig, params: RunParams) -> PipelineResult:
    """Run the configured pipeline: user steps, per-session namespace, GC, tracing."""
    from .harness import PipelineHarness  # deferred: costs ~160 ms of import at startup

    with PipelineHarness(config.name, params.project_dir) as harness:
        state = params.to_state(
            materialize_prompt=harness.materialize_prompt,
            scratch_dir=harness.namespace,
        )
        return PipelineResult.from_state(harness.run(state))


def run_subpipeline(config: PipelineConfig, params: SubpipelineParams) -> PipelineResult:
    """Run a pipeline inside a session that already exists, without the harness.

    The harness opens a per-session namespace and sweeps it when the run ends. A
    finalize pipeline runs *within* a session whose directory is already there and
    stages nothing, so the harness would create a directory nothing writes to and
    then delete it. Declared here so "run one without the harness" is a capability
    with a name, rather than something a caller gets by importing ``loader`` and
    ``pipeline`` directly (ADR-0026 D3).
    """
    from .loader import load_core_pipeline
    from .pipeline import run as run_steps

    final = run_steps(load_core_pipeline(config.name), dict(params.to_state()))
    return PipelineResult.from_state(final)


def warm(config: PipelineConfig) -> None:
    """Parse and build ``config`` now, so running it later touches no disk.

    For a caller that will run a pipeline at the end of a session it is about to
    start (HATS-566): on an editable install the YAML on disk can be replaced
    mid-session by a ``git pull``, and it would then be read against the step
    modules this process already holds. Doing it up front freezes both together —
    since HATS-1783 the step modules are imported by the same act, so this pins the
    ids as well as the file. Failure is the caller's to report: the sub-pipeline is
    best-effort and warming it is not the moment to end a session.
    """
    from .loader import load_core_pipeline

    load_core_pipeline(config.name)
