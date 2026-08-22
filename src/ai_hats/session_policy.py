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
from typing import Any, Mapping

from ai_hats_wt import IsolationMode

from .debt import CompositionPayload, SessionManager, TracerFactory
from .pipeline import PipelineResult, PromptWriter


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

    def to_state(self, *, materialize_prompt: PromptWriter) -> dict[str, Any]:
        harness = self.harness
        state: dict[str, Any] = {
            "role": self.role.name,
            "composition": self.role.composition,
            "project_dir": self.project_dir,
            "session_mgr": self.recording.manager,
            "tracer_factory": self.recording.tracer_factory,
            "tags": dict(self.annotations) if self.annotations else None,
            "interactive": isinstance(harness, Hitl),
            "prompt_path": materialize_prompt(harness.prompt),
        }
        if isinstance(harness, Hitl):
            state["extra_args"] = list(harness.extra_args)
        if isinstance(harness, Automate):
            state["model"] = harness.model
            state["isolation"] = harness.isolation.value
            state["ticket"] = harness.ticket_id
        return state


@dataclass(frozen=True)
class SessionOutcome:
    """What a session run gives its caller back."""

    exit_code: int | None = None
    session_id: str | None = None
    session_dir: Path | None = None

    @classmethod
    def of(cls, result: PipelineResult) -> SessionOutcome:
        session = result.session
        return cls(
            exit_code=result.exit_code,
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
