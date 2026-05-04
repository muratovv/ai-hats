"""Proposal schema (PROP-NNN.yaml) — improvement suggestions from reflect-session.

Proposals live in `.agent/backlog/proposals/PROP-NNN.yaml` next to tasks/.
Status field regulates visibility — no physical archive directory.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProposalCategory = Literal["rule", "skill", "code", "process", "doc"]
ProposalStatus = Literal["open", "accepted", "rejected", "deferred", "duplicate"]


class Vote(BaseModel):
    """Single vote on a proposal from one reflect-session run."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    judge_session_id: str | None = None
    timestamp: datetime
    reasoning: str = Field(..., min_length=1)


class Proposal(BaseModel):
    """PROP-NNN entry in the proposal backlog."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(..., pattern=r"^PROP-\d+$")
    created: datetime
    title: str = Field(..., min_length=1)
    category: ProposalCategory
    target: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    related_hypotheses: list[str] = Field(default_factory=list)
    votes: list[Vote] = Field(default_factory=list)
    status: ProposalStatus = "open"
    failed_session_id: str | None = Field(
        default=None,
        description=(
            "Set only on meta-proposals (category=process, target=reflect-session) "
            "where reflect-session failed — points to the session that needs retry."
        ),
    )
