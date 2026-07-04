"""Harness + feedback config schema (channel, session-retro thresholds)."""

from __future__ import annotations

import sys
from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from ai_hats_core import YamlModel as _YamlModel

class FeedbackPolicy(str, Enum):
    OFF = "off"
    ALWAYS = "always"
    SMART = "smart"
    HINT = "hint"


class Channel(str, Enum):
    """Harness source channel (HATS-764). Maps an audience to an install spec.

    - ``local``  — ai-hats dev: editable install of a working tree; moving target.
    - ``edge``   — own repos: ``git+https://<repo>@<branch-HEAD-sha>``; moving target.
    - ``stable`` — end users: ``ai-hats==<latest-tag>`` from PyPI; pinned, semver-monotonic.
    """

    LOCAL = "local"
    EDGE = "edge"
    STABLE = "stable"


class SmartThreshold(_YamlModel):
    min_turns: int = 5
    min_tool_calls: int = 10


class SessionRetroConfig(_YamlModel):
    policy: FeedbackPolicy = FeedbackPolicy.SMART
    smart_threshold: SmartThreshold = Field(default_factory=SmartThreshold)
    background: bool = True
    # Optional model override for the single session-reviewer LLM call (HATS-252).
    # When None, the provider CLI's default model is used.
    review_model: str | None = None
    # Deprecated alias retained for back-compat with pre-HATS-252 ai-hats.yaml
    # files (`reflect_model:`). When `review_model` is unset and this field is
    # present, the validator copies it across and emits a DeprecationWarning.
    reflect_model: str | None = None

    @model_validator(mode="after")
    def _alias_reflect_model(self) -> "SessionRetroConfig":
        if self.review_model is None and self.reflect_model is not None:
            import warnings

            warnings.warn(
                "feedback.session_retro.reflect_model is deprecated; rename to review_model.",
                DeprecationWarning,
                stacklevel=2,
            )
            self.review_model = self.reflect_model
        return self


class FeedbackConfig(_YamlModel):
    session_retro: SessionRetroConfig = Field(default_factory=SessionRetroConfig)

    @property
    def is_default(self) -> bool:
        return self == FeedbackConfig()


class HarnessConfig(_YamlModel):
    """Harness source — where ``ai-hats self update`` pulls ai-hats from (HATS-764).

    - ``channel`` — ``local`` | ``edge`` | ``stable`` (default ``stable``). An
      unknown value fails loud via the :class:`Channel` enum.
    - ``repo`` — edge-only override of the upstream repo URL
      (precedence ``AI_HATS_REPO_URL`` env > this field > default upstream https).
    - ``path`` — local-only editable source path (defaults to the project root).

    Inherits ``extra="ignore"`` from :class:`_YamlModel` (NOT ``forbid``): a
    newer ai-hats may add a nested ``harness`` sub-field, and an older binary
    must drop it rather than crash (forward-compat — the top-level strip in
    :meth:`ProjectConfig._strip_unknown_fields` only reaches the outer
    ``harness`` key, never nested ones). The drop is WARNed (below), mirroring
    the top-level strip so a vanished field is observable, not silent.
    """

    channel: Channel = Channel.STABLE
    repo: str | None = None
    path: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _warn_unknown_keys(cls, data: Any) -> Any:
        """WARN (stderr) on an unknown nested key before ``extra="ignore"`` drops
        it — keeps the forward-compat behaviour observable, consistent with
        ``ProjectConfig._strip_unknown_fields`` (HATS-764 review)."""
        if isinstance(data, dict):
            for key in sorted(set(data) - set(cls.model_fields)):
                print(
                    f"WARN: ai-hats.yaml harness: dropping unknown field {key!r} "
                    "(not in this ai-hats version's schema — written by a newer "
                    "ai-hats? run 'ai-hats self update' to use it).",
                    file=sys.stderr,
                )
        return data

    @property
    def is_default(self) -> bool:
        return self == HarnessConfig()

    def to_dict(self) -> dict[str, Any]:
        # Omit None repo/path so a plain `channel: edge` block stays minimal.
        d: dict[str, Any] = {"channel": self.channel.value}
        if self.repo is not None:
            d["repo"] = self.repo
        if self.path is not None:
            d["path"] = self.path
        return d
