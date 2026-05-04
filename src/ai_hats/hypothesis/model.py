"""Hypothesis schema (HYP-NNN.yaml) — single source of truth for HYP backlog.

Hypotheses live in `.agent/hypotheses/HYP-NNN.yaml`. Schema is intentionally
permissive on extras (legacy entries pre-HATS-210 carry custom keys we want
to preserve). New entries written via `ai-hats hyp append-verdict` follow
the strict ValidationLogEntry shape.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VerdictKind = Literal["confirmed", "refuted", "inconclusive", "n/a"]
RecommendationKind = Literal[
    "close_confirmed",
    "close_refuted",
    "keep",
    "extend_window",
]
HypothesisStatus = Literal["active", "confirmed", "refuted", "stalled"]


class Baseline(BaseModel):
    """Pre-change observation block — shape varies across HYP files; allow extras."""

    model_config = ConfigDict(extra="allow")

    source: str | None = None
    observation: str | None = None


class ExitCriteria(BaseModel):
    """Conditions to confirm/refute/stall the hypothesis."""

    model_config = ConfigDict(extra="forbid")

    confirm: list[str] = Field(default_factory=list)
    refute: list[str] = Field(default_factory=list)
    stalled: list[str] = Field(default_factory=list)


class ValidationLogEntry(BaseModel):
    """One verdict entry written by reflect-session via CLI.

    Allows extras to preserve legacy free-form entries (sweep_report, sample,
    bundle, etc.) — new entries from `ai-hats hyp append-verdict` populate
    only the typed fields below.
    """

    model_config = ConfigDict(extra="allow")

    date: date
    verdict: VerdictKind
    evidence: str = Field(..., min_length=1)
    recommendation: RecommendationKind = "keep"
    session_id: str | None = None
    judge_session_id: str | None = None
    timestamp: datetime | None = None


class Hypothesis(BaseModel):
    """HYP-NNN backlog entry. Permissive on extras to survive schema evolution."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str = Field(..., pattern=r"^HYP-\d+$")
    title: str = Field(..., min_length=1)
    status: HypothesisStatus
    created: date
    source_task: str = Field(..., min_length=1)

    hypothesis: str = Field(..., min_length=1)
    baseline: Baseline | str | None = None
    expected_outcome: list[str] = Field(default_factory=list)
    observation_window: str | None = None
    success_criterion: str | None = None
    rollback_condition: str | None = None

    validation_log: list[ValidationLogEntry] = Field(default_factory=list)
    exit_criteria: ExitCriteria | None = None
    min_sessions_per_bundle: int = 4
    freshness_rule: str | None = None

    last_rule_revision_date: date | None = None
    last_judge_protocol_revision_date: date | None = None

    closed: date | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    next_observation_blocked_by: str | list[str] | None = None
    change: str | None = None
