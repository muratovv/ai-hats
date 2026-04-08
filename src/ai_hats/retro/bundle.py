"""BundleV1: first-class artifact grouping session retros for judge analysis.

A bundle is a thin pointer object — it lists the session_ids the judge will
analyze together, plus optional free-form `notes`. Bundles live in
`.agent/retrospectives/bundles/` as standalone YAML files (no markdown body).

Bundles are deliberately lens-agnostic: the same set of sessions can be judged
multiple times with different `--focus` lenses, each producing a separate
JudgeRetroV1. The bundle is the data subject; focus is a research question.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "hats-bundle/v1"


class BundleV1(BaseModel):
    """Set of sessions queued for judge analysis.

    The bundle_id pattern is `BUNDLE-YYYY-MM-DD-NNN` where NNN is a
    daily counter (resets each calendar day). This makes ids both
    chronologically sortable and human-readable.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["hats-bundle/v1"] = Field(..., alias="schema")
    bundle_id: str = Field(
        ...,
        pattern=r"^BUNDLE-\d{4}-\d{2}-\d{2}-\d{3}$",
        description="e.g. BUNDLE-2026-04-08-001",
    )
    project: str = Field(..., min_length=1)
    created: datetime
    session_ids: list[str] = Field(..., min_length=1)
    notes: str | None = None
