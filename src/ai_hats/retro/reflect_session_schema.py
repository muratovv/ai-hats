"""ReflectSessionV1: per-session reflect-session output (HATS-210).

Single-session judge run produces:
  - hypothesis_verdicts: vote per active HYP (count must match active set)
  - proposal_actions: list of created/voted proposals (audit trail)
  - self_problems: refs to meta-proposals filed when judge couldn't comply
                   with format or hit a meta-issue

Frontmatter + body file format. Stored under
`.agent/retrospectives/reflect-session/<session_id>.md`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "hats-reflect-session/v1"
SCHEMA_FAMILY = "hats-reflect-session"

VerdictKind = Literal["confirmed", "refuted", "inconclusive", "n/a"]
RecommendationKind = Literal[
    "close_confirmed", "close_refuted", "keep", "extend_window"
]
ProposalActionKind = Literal["created", "voted"]


class HypothesisVerdict(BaseModel):
    """One verdict for one active hypothesis."""

    model_config = ConfigDict(extra="forbid")

    hyp_id: str = Field(..., pattern=r"^HYP-\d+$")
    verdict: VerdictKind
    evidence: str = Field(..., min_length=1)
    recommendation: RecommendationKind = "keep"


class ProposalAction(BaseModel):
    """One proposal action taken during this judge run."""

    model_config = ConfigDict(extra="forbid")

    action: ProposalActionKind
    prop_id: str = Field(..., pattern=r"^PROP-\d+$")


class ReflectSessionV1(BaseModel):
    """Per-session reflect-session output."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["hats-reflect-session/v1"] = Field(..., alias="schema")
    session_id: str = Field(..., min_length=1)
    timestamp: datetime
    hypothesis_verdicts: list[HypothesisVerdict] = Field(default_factory=list)
    proposal_actions: list[ProposalAction] = Field(default_factory=list)
    self_problems: list[str] = Field(
        default_factory=list,
        description="PROP-NNN refs to meta-proposals filed by judge",
    )
