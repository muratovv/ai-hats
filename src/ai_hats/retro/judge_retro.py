"""JudgeRetroV1: analytical retro produced by judge over a bundle.

Output of one judge invocation: structured findings (each with mandatory
evidence pointing into specific sessions of the bundle), patterns to keep,
and an optional self-critique of the judge's own analysis quality.

Each finding may carry a ProposedFix with structured target (FixTarget) so
that L2 (aggregator) and L4 (cross-project upstream) can filter by component
type without parsing free-form strings.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import Finding

SCHEMA_VERSION = "hats-judge-retro/v1"


class JudgeRetroV1(BaseModel):
    """Analytical retro — one judge invocation over a bundle of sessions.

    The link to source data goes through `bundle_id` (BundleV1 artifact),
    not through embedded session ids — bundles are first-class so the same
    bundle can be re-judged with different focus.

    `judge_run_id` is a simple unique string (not necessarily a session id)
    to allow synthetic/non-session judge runs (e.g. CI batch analysis).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["hats-judge-retro/v1"] = Field(..., alias="schema")
    judge_run_id: str = Field(
        ..., min_length=1, description="Unique judge invocation id"
    )
    project: str = Field(..., min_length=1)
    date: date
    bundle_id: str = Field(
        ...,
        pattern=r"^BUNDLE-\d{4}-\d{2}-\d{2}-\d{3}$",
        description="Reference to BundleV1 artifact",
    )
    findings: list[Finding] = Field(..., min_length=1)
    patterns_to_keep: list[str] = Field(default_factory=list)
    meta_critique: str | None = Field(
        None, description="Judge's self-assessment of own analysis quality"
    )
