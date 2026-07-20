"""Parser for `reflect-issue` pipeline output (HATS-1044 R6: integrator-owned).

The `hypothesis-intake` role emits a YAML block between
``BEGIN_INTAKE_RESULT``/``END_INTAKE_RESULT`` markers; the `extract_marker`
pipeline step strips them and this module parses the inner YAML into a typed
``IntakeResult`` (Create | Merge) for `ai-hats reflect issue` to act on. Pure
YAML/pydantic — no storage, no LLM call (that lives in the pipeline's
`launch_provider` step). Lives on the integrator side so `reflect` no longer
reaches into ``ai_hats_tracker`` (the retire-gate direction).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class IntakeDraft(BaseModel):
    """Fields a Haiku-draft must supply when proposing a new HYP."""

    model_config = ConfigDict(extra="allow")

    title: str = Field(..., min_length=1, max_length=200)
    hypothesis: str = Field(..., min_length=1)
    baseline: str | None = None
    expected_outcome: list[str] = Field(default_factory=list)
    success_criterion: str | None = None
    exit_criteria: dict[str, list[str]] | None = None


class CreateAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["create"]
    draft: IntakeDraft


class MergeAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["merge"]
    target_id: str = Field(..., pattern=r"^HYP-\d+$")
    evidence: str = Field(..., min_length=1)


IntakeResult = Annotated[
    Union[CreateAction, MergeAction],
    Field(discriminator="action"),
]


class IntakeParseError(ValueError):
    """Raised when the marker block content is not a valid IntakeResult."""


def parse_intake_yaml(text: str) -> CreateAction | MergeAction:
    """Parse the YAML payload emitted by the `hypothesis-intake` role."""
    if not text or not text.strip():
        raise IntakeParseError("empty intake result")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise IntakeParseError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise IntakeParseError(f"expected YAML mapping at top level, got {type(data).__name__}")
    action = data.get("action")
    try:
        if action == "create":
            return CreateAction.model_validate(data)
        if action == "merge":
            return MergeAction.model_validate(data)
    except ValidationError as exc:
        raise IntakeParseError(f"schema mismatch: {exc}") from exc
    raise IntakeParseError(f"unknown action {action!r} (expected 'create' or 'merge')")
