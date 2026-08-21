"""Public contract of the pipeline area: what a caller names, and what it reads back.

Import-light on purpose — an enum, two dataclasses and the funnel mapping. Naming a
pipeline must not cost the YAML loader and the step registry: those live behind
``launch`` (HATS-1783).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from . import keys as _keys


class PipelineId(str, Enum):
    """The core pipelines a caller may launch."""

    HUMAN = _keys.PIPELINE_HUMAN
    EXECUTE = _keys.PIPELINE_EXECUTE
    INIT = _keys.PIPELINE_INIT
    FINALIZE_HITL = _keys.PIPELINE_FINALIZE_HITL
    FINALIZE_SUBAGENT = _keys.PIPELINE_FINALIZE_SUBAGENT
    REFLECT_SESSION = _keys.PIPELINE_REFLECT_SESSION
    REFLECT_ALL = _keys.PIPELINE_REFLECT_ALL
    REFLECT_HYPOTHESIS_PHASE1 = _keys.PIPELINE_REFLECT_HYPOTHESIS_PHASE1
    REFLECT_HYPOTHESIS_PHASE2 = _keys.PIPELINE_REFLECT_HYPOTHESIS_PHASE2
    REFLECT_ROLE = _keys.PIPELINE_REFLECT_ROLE
    REFLECT_ISSUE = _keys.PIPELINE_REFLECT_ISSUE


@dataclass(frozen=True)
class RoleSessionRequest:
    """Launch a composed role in a provider session.

    ``composition`` / ``session_mgr`` / ``tracer_factory`` are typed ``object``: the
    area carries them to the steps and never reads them.
    """

    role: str
    project_dir: Path
    composition: object
    session_mgr: object
    tracer_factory: object
    interactive: bool = False
    prompt_path: Path | None = None
    model: str = ""
    isolation: str = ""
    ticket: str = ""
    tags: Mapping[str, str] | None = None
    extra_args: tuple[str, ...] | None = None

    def to_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            _keys.KEY_ROLE: self.role,
            _keys.KEY_INTERACTIVE: self.interactive,
            _keys.KEY_PROJECT_DIR: self.project_dir,
            _keys.KEY_PROMPT_PATH: self.prompt_path,
            _keys.KEY_MODEL: self.model,
            _keys.KEY_ISOLATION: self.isolation,
            _keys.KEY_TICKET: self.ticket,
            _keys.KEY_TAGS: dict(self.tags) if self.tags else None,
            _keys.KEY_COMPOSITION: self.composition,
            _keys.KEY_SESSION_MGR: self.session_mgr,
            _keys.KEY_TRACER_FACTORY: self.tracer_factory,
        }
        # None means "not seeded" (ADR-0005 §3): the Automate path never offered
        # extra_args, and seeding an empty list there would invent a value.
        if self.extra_args is not None:
            state[_keys.KEY_EXTRA_ARGS] = list(self.extra_args)
        return state


@dataclass(frozen=True)
class PipelineOutcome:
    """What a caller reads back; an absent value stays ``None``, per the funnel rule."""

    exit_code: int | None = None
    session_id: str | None = None
    session_dir: Path | None = None

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "PipelineOutcome":
        code = state.get(_keys.KEY_EXIT_CODE)
        return cls(
            exit_code=None if code is None else int(code),
            session_id=state.get(_keys.KEY_SESSION_ID),
            session_dir=state.get(_keys.KEY_SESSION_DIR),
        )

    def exit_code_or(self, default: int) -> int:
        return default if self.exit_code is None else self.exit_code

    def require_session(self) -> tuple[str, Path]:
        """Session identity, or the loud failure the report used to get from ``final[…]``."""
        if self.session_id is None or self.session_dir is None:
            raise KeyError("pipeline produced no session_id / session_dir")
        return self.session_id, self.session_dir


class PipelineSession:
    """One launched pipeline: its scratch space and its dispatch."""

    def __init__(self, harness: Any) -> None:
        self._harness = harness

    @property
    def scratch_dir(self) -> Path:
        return self._harness.namespace

    def materialize_prompt(self, text: str | None) -> Path | None:
        return self._harness.materialize_prompt(text)

    def run(self, request: RoleSessionRequest) -> PipelineOutcome:
        return PipelineOutcome.from_state(self._harness.run(request.to_state()))
