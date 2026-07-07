"""Worktree base/merge-target config (HATS-942) — fork/dogfood workflows.

:class:`WorktreeConfig` lets a project point the worktree FSM at a base branch
to cut task worktrees FROM and a separate merge target to land them INTO
(base != merge-target), instead of the hardcoded canonical ``("master","main")``.
Both fields unset => today's behavior verbatim (cut from the main-repo HEAD,
merge into the HEAD-following canonical branch).
"""

from __future__ import annotations

import sys
from typing import Any

from pydantic import model_validator

from ai_hats_core import YamlModel as _YamlModel


class WorktreeConfig(_YamlModel):
    """``worktree:`` section of ai-hats.yaml (HATS-942).

    ``base_branch`` = start-point new task worktrees are cut FROM (unset => HEAD);
    ``merge_target`` = branch ``wt merge`` lands INTO + the create-time HEAD guard
    requires (unset => today's canonical set-membership). ``extra="ignore"`` +
    ``_warn_unknown_keys`` mirror ``HarnessConfig``: an older binary DROPs a newer
    nested ``worktree.*`` field (WARN, no crash) — the top-level unknown-key strip
    never reaches nested keys. A missing *branch* is a separate fail-loud resolver
    check (R6), not an unknown-*key* concern.
    """

    base_branch: str | None = None
    merge_target: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _warn_unknown_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in sorted(set(data) - set(cls.model_fields)):
                print(
                    f"WARN: ai-hats.yaml worktree: dropping unknown field {key!r} "
                    "(not in this ai-hats version's schema — written by a newer "
                    "ai-hats? run 'ai-hats self update' to use it).",
                    file=sys.stderr,
                )
        return data

    @property
    def is_default(self) -> bool:
        return self == WorktreeConfig()

    def to_dict(self) -> dict[str, Any]:
        # Omit None fields so a partial `worktree:` block stays minimal.
        d: dict[str, Any] = {}
        if self.base_branch is not None:
            d["base_branch"] = self.base_branch
        if self.merge_target is not None:
            d["merge_target"] = self.merge_target
        return d
