"""Public contract of the pipeline area: run a materialized pipeline config.

The area knows how to run step1 -> ... -> stepN and nothing about who runs what:
which pipelines exist is the application's catalog, not this module's enum
(ADR-0026 D14). Import-light on purpose — dataclasses and the funnel mapping only;
``run_pipeline`` pulls the harness inside its body (HATS-1783).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from . import keys as _keys


@dataclass(frozen=True)
class PipelineConfig:
    """Which pipeline to run — the materialized ``pipeline.yaml``."""

    name: str


@dataclass(frozen=True)
class RoleParams:
    """The materialized role. ``composition`` is opaque here: the steps read it."""

    name: str
    composition: object


@dataclass(frozen=True)
class SessionParams:
    """The session the run belongs to: where it happens and how it is recorded."""

    project_dir: Path
    manager: object
    tracer_factory: object
    tags: Mapping[str, str] | None = None
    ticket: str = ""
    isolation: str = ""


@dataclass(frozen=True)
class HarnessParams:
    """How the harness is launched. The branch is the type, never a flag."""

    prompt: str | None = None
    model: str = ""


@dataclass(frozen=True)
class Hitl(HarnessParams):
    """Human-in-the-loop: the provider CLI takes over the terminal.

    ``extra_args`` lives here and only here — the Automate branch drops them
    (``steps/launch.py``), so on that branch the field must not be expressible.
    """

    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class Automate(HarnessParams):
    """Non-interactive: a captured subprocess, reported when it exits."""


@dataclass(frozen=True)
class RunParams:
    """Everything about this run, and nothing about which pipeline it is."""

    role: RoleParams
    session: SessionParams
    harness: HarnessParams = field(default_factory=Automate)

    def to_state(self, *, materialize_prompt: Callable[[str | None], Path | None]) -> dict[str, Any]:
        harness = self.harness
        state: dict[str, Any] = {
            _keys.KEY_ROLE: self.role.name,
            _keys.KEY_COMPOSITION: self.role.composition,
            _keys.KEY_PROJECT_DIR: self.session.project_dir,
            _keys.KEY_SESSION_MGR: self.session.manager,
            _keys.KEY_TRACER_FACTORY: self.session.tracer_factory,
            _keys.KEY_TAGS: dict(self.session.tags) if self.session.tags else None,
            _keys.KEY_TICKET: self.session.ticket,
            _keys.KEY_ISOLATION: self.session.isolation,
            _keys.KEY_INTERACTIVE: isinstance(harness, Hitl),
            _keys.KEY_MODEL: harness.model,
            _keys.KEY_PROMPT_PATH: materialize_prompt(harness.prompt),
        }
        if isinstance(harness, Hitl):
            state[_keys.KEY_EXTRA_ARGS] = list(harness.extra_args)
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

    exit_code: int | None = None
    session: SessionRef | None = None
    # Step name -> the exception a ``failure_policy=continue`` step swallowed,
    # as the runner records it (``pipeline.py``).
    errors: Mapping[str, BaseException] = field(default_factory=dict)

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "PipelineResult":
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

    with PipelineHarness(config.name, params.session.project_dir) as harness:
        state = params.to_state(materialize_prompt=harness.materialize_prompt)
        return PipelineResult.from_state(harness.run(state))
