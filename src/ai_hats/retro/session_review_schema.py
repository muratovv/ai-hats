"""SessionReviewV1: unified post-session artifact (HATS-252).

One canonical schema replacing the v1 split between SessionRetroV1 (facts) and
ReflectSessionV1 (analysis). Pure-Python computes the factual fields; a single
LLM call (role: session-reviewer) produces the analysis fields. The runner
merges the two and writes the resulting document to
``.agent/retrospectives/sessions/<session_id>.md``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import SessionArtifacts, SessionLinks, SessionMetrics
from .reflect_session_schema import HypothesisVerdict, ProposalAction

SCHEMA_VERSION = "hats-session-review/v1"
SCHEMA_FAMILY = "hats-session-review"


class SessionReviewV1(BaseModel):
    """Single per-session artifact: facts (pure-Python) + analysis (LLM)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["hats-session-review/v1"] = Field(..., alias="schema")

    # Identity (factual)
    session_id: str = Field(..., min_length=1)
    project: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    date: date
    timestamp: datetime

    # Facts (pure-Python; LLM never writes these)
    metrics: SessionMetrics
    artifacts: SessionArtifacts = Field(default_factory=SessionArtifacts)
    links: SessionLinks

    # Analysis (LLM output)
    summary: str = Field(..., min_length=1, description="What was done — narrative")
    observations: list[str] = Field(
        default_factory=list,
        description="Free-form behavioural notes (0–6 bullets typical)",
    )
    hypothesis_verdicts: list[HypothesisVerdict] = Field(default_factory=list)
    proposal_actions: list[ProposalAction] = Field(default_factory=list)
    self_problems: list[str] = Field(
        default_factory=list,
        description="PROP-NNN refs to meta-proposals filed during this run",
    )
