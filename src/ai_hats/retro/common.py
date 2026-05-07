"""Shared types for retro schemas: session-level snapshots used by SessionReviewV1."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
