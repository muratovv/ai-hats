"""Shared types for retro schemas: enums, models, and validators.

Used by session_retro.SessionRetroV1, bundle.BundleV1, and judge_retro.JudgeRetroV1.
All types use ConfigDict(extra="forbid") for strict validation — unknown fields
cause errors rather than silent drops, which is essential for the longitudinal
feedback loop where field drift would invalidate measurements.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --- enums ---


class Category(str, Enum):
    """Classification of a finding's root cause."""

    KNOWLEDGE = "knowledge"
    ENVIRONMENT = "environment"
    PROCESS = "process"
    COMMUNICATION = "communication"
    ASSUMPTION = "assumption"
    TOOLING = "tooling"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(str, Enum):
    OPEN = "open"
    APPLIED = "applied"
    TRACKED = "tracked"
    REJECTED = "rejected"


class FixType(str, Enum):
    SKILL_UPDATE = "skill_update"
    SKILL_CREATE = "skill_create"
    RULE_UPDATE = "rule_update"
    RULE_CREATE = "rule_create"
    MEMORY = "memory"
    PROJECT_CLAUDE_MD = "project_claude_md"
    CODE_CHANGE = "code_change"
    NO_ACTION = "no_action"


class FixTargetKind(str, Enum):
    SKILL = "skill"
    RULE = "rule"
    TRAIT = "trait"
    MEMORY = "memory"
    PROJECT_MD = "project_md"
    CODE = "code"
    EXTERNAL = "external"


class EvidenceSource(str, Enum):
    AUDIT = "audit"
    METRICS = "metrics"
    SESSION_RETRO = "session_retro"
    GIT = "git"
    EXTERNAL = "external"


# --- module-private constants ---


_FIX_TYPES_REQUIRING_TARGET = frozenset({
    FixType.SKILL_UPDATE,
    FixType.SKILL_CREATE,
    FixType.RULE_UPDATE,
    FixType.RULE_CREATE,
    FixType.MEMORY,
    FixType.PROJECT_CLAUDE_MD,
    FixType.CODE_CHANGE,
})


_SKILL_RULE_FIX_TYPES = frozenset({
    FixType.SKILL_UPDATE,
    FixType.SKILL_CREATE,
    FixType.RULE_UPDATE,
    FixType.RULE_CREATE,
})


# --- finding building blocks ---


class FixTarget(BaseModel):
    """Structured target reference for a ProposedFix.

    Used by L2 (aggregator) to filter fixes by component type and by L4
    (cross-project upstream) to decide which IMPs are framework candidates.
    """

    model_config = ConfigDict(extra="forbid")

    kind: FixTargetKind
    name: str = Field(
        ...,
        min_length=1,
        description="Identifier within kind, e.g. 'judge-protocol' or 'src/cli.py'",
    )


class Evidence(BaseModel):
    """Evidence for a finding, always carrying its source session_id.

    session_id is required because judge bundles may span multiple sessions,
    and findings need to be traceable back to the exact session that produced
    each piece of evidence.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    source: EvidenceSource
    location: str = Field(
        ..., min_length=1, description="e.g. 'audit.md:Turn 4' or 'metrics.json:tool_calls'"
    )
    quote: str | None = None


class ExpectedImpact(BaseModel):
    """Measurable expectation for a fix, used by the longitudinal validation cycle.

    After applying the fix, observe the next observation_window_retros judge
    runs and check that the matching finding category drops to
    target_frequency_after.
    """

    model_config = ConfigDict(extra="forbid")

    reduces_category: Category | None = None
    reduces_root_cause_pattern: str | None = Field(
        None,
        description="Substring or regex matched against root_cause of future findings",
    )
    target_frequency_after: float = Field(0.0, ge=0.0, le=1.0)
    observation_window_retros: int = Field(10, ge=1)


class ProposedFix(BaseModel):
    """Proposed remedy for a finding."""

    model_config = ConfigDict(extra="forbid")

    type: FixType
    target: FixTarget | None = None
    description: str = Field(..., min_length=1)
    expected_impact: ExpectedImpact | None = None

    @model_validator(mode="after")
    def _target_required_for_typed_fixes(self) -> ProposedFix:
        if self.type in _FIX_TYPES_REQUIRING_TARGET and self.target is None:
            raise ValueError(
                f"target is required for fix type {self.type.value}"
            )
        return self


class Finding(BaseModel):
    """Single judge finding with mandatory evidence and optional proposed fix.

    The id pattern intentionally allows letter suffixes (F1, F5b) so judges
    can split a finding into related sub-findings without renumbering.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^F\d+[a-z]?$", description="F1, F2, F5b, ...")
    title: str = Field(..., min_length=1)
    category: Category
    severity: Severity
    cost_minutes: float | None = Field(None, ge=0)
    root_cause: str = Field(..., min_length=1)
    evidence: list[Evidence] = Field(..., min_length=1)
    proposed_fix: ProposedFix | None = None
    status: FindingStatus = FindingStatus.OPEN
    task_ref: str | None = Field(None, description="Backlog task id, e.g. PGPT-301")

    @model_validator(mode="after")
    def _tracked_requires_task_ref(self) -> Finding:
        if self.status == FindingStatus.TRACKED and not self.task_ref:
            raise ValueError(
                f"finding {self.id}: status=tracked requires task_ref"
            )
        return self

    @model_validator(mode="after")
    def _skill_rule_requires_expected_impact(self) -> Finding:
        if (
            self.status == FindingStatus.TRACKED
            and self.proposed_fix is not None
            and self.proposed_fix.type in _SKILL_RULE_FIX_TYPES
            and self.proposed_fix.expected_impact is None
        ):
            raise ValueError(
                f"finding {self.id}: expected_impact is required for tracked "
                f"skill/rule fixes (type={self.proposed_fix.type.value})"
            )
        return self


# --- session-level shared types ---


class SessionMetrics(BaseModel):
    """Snapshot of metrics.json relevant for retro analysis.

    Duplicated into the retro frontmatter so retros are self-contained
    and can be shipped to gist/PR without needing the original .gitlog/.
    """

    model_config = ConfigDict(extra="forbid")

    exit_code: int
    turns: int = Field(..., ge=0)
    tool_calls: int = Field(..., ge=0)
    tokens_in: int = Field(0, ge=0)
    tokens_out: int = Field(0, ge=0)
    cache_read: int = Field(0, ge=0)
    cache_creation: int = Field(0, ge=0)
    duration_wall_minutes: float | None = Field(None, ge=0)
    duration_active_minutes: float | None = Field(None, ge=0)
    tasks_closed: int | None = Field(None, ge=0)
    commits: int | None = Field(None, ge=0)

    @property
    def cache_hit_ratio(self) -> float | None:
        denom = self.cache_read + self.tokens_in
        return self.cache_read / denom if denom else None

    @property
    def tool_calls_per_turn(self) -> float | None:
        return self.tool_calls / self.turns if self.turns else None


class SessionLinks(BaseModel):
    """Pointers to source artifacts for navigation from a retro back to raw data."""

    model_config = ConfigDict(extra="forbid")

    audit: str = Field(..., min_length=1)
    metrics: str | None = None
    worktree: str | None = None


class SessionArtifacts(BaseModel):
    """Concrete outputs the session produced — facts only, no analysis."""

    model_config = ConfigDict(extra="forbid")

    files_changed: list[str] = Field(default_factory=list)
    tasks_closed: list[str] = Field(default_factory=list)
    commits: list[str] = Field(default_factory=list)
