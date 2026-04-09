"""Aggregation schema: cross-session pattern surfacing from judge retros.

AggregationV1 captures the output of clustering judge findings — which
patterns recur, at what frequency, and what fixes have been proposed.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import Category, FixTarget, ProposedFix, Severity

SCHEMA_VERSION = "hats-aggregation/v1"


class FindingRef(BaseModel):
    """Pointer to a specific finding in a specific judge retro."""

    model_config = ConfigDict(extra="forbid")

    judge_retro_file: str = Field(..., min_length=1)
    finding_id: str = Field(..., pattern=r"^F\d+[a-z]?$")


class FindingClusterSummary(BaseModel):
    """One cluster of related findings across judge retros."""

    model_config = ConfigDict(extra="forbid")

    cluster_id: str = Field(..., pattern=r"^C\d+$")
    representative_title: str = Field(..., min_length=1)
    category: Category
    severity: Severity
    target: FixTarget | None = None
    root_cause_pattern: str = Field(..., min_length=1)
    frequency: int = Field(..., ge=1)
    rate: float = Field(..., ge=0.0, le=1.0)
    source_findings: list[FindingRef] = Field(..., min_length=1)
    proposed_fix: ProposedFix | None = None


class AggregationV1(BaseModel):
    """Cross-session aggregation of judge findings."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["hats-aggregation/v1"] = Field(
        SCHEMA_VERSION, alias="schema"
    )
    aggregation_id: str = Field(
        ..., pattern=r"^AGG-\d{4}-\d{2}-\d{2}-\d{3}$"
    )
    project: str = Field(..., min_length=1)
    date: date
    strategy: str = Field(..., min_length=1)
    retros_analyzed: int = Field(..., ge=1)
    since: date | None = None
    clusters: list[FindingClusterSummary] = Field(default_factory=list)
