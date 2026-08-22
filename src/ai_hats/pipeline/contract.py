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

    # TODO(HATS-1783): grows a ``path`` when the run_yaml consumers migrate —
    # project-local pipelines are loaded by path, not by library name.
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


@dataclass(frozen=True)
class SessionRef:
    """A session the run produced — the handle a later link of the chain refers to."""

    id: str
    dir: Path
    provider_session_id: str | None = None


@dataclass(frozen=True)
class PipelineResult:
    """What the run produced and what went wrong on the way."""

    # None when no step reported one — the run launched no session, or the step that
    # launches it failed under ``failure_policy=continue`` and the run went on.
    # Callers name their own default through ``exit_code_or``.
    exit_code: int | None = None
    session: SessionRef | None = None
    # Step name -> the exception a ``failure_policy=continue`` step swallowed,
    # as the runner records it (``pipeline.py``).
    errors: Mapping[str, BaseException] = field(default_factory=dict)
    # What the run left in the funnel, by the name the step that wrote it declared —
    # the area carries the map and reads none of it. The typed readers live in
    # ``session_policy.py``, for the launches whose value is neither code nor session.
    produced: Mapping[str, Any] = field(default_factory=dict)

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
            produced=dict(state),
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
        state = params.to_state(
            materialize_prompt=harness.materialize_prompt,
            scratch_dir=harness.namespace,
        )
        return PipelineResult.from_state(harness.run(state))
