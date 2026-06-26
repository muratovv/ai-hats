"""``render_update_banner`` step — print Update banner from cache to stderr.

Runs at the tail of ``execute`` / ``human`` pipelines, immediately after
``launch_provider`` has emitted the Session-summary block. Reads whatever's
in ``<ai_hats_dir>/.cache/update-check.json`` (the latest probe result, even
if technically stale) and prints a three-line banner when the installed
SHA differs from the upstream master SHA.

Glossary (see ``docs/glossary.md``):
- **Session summary** — the ``✨ Session <id> complete!`` block printed by
  ``runtime._print_session_end`` from inside ``launch_provider``.
- **Update banner** — the block produced here.

``failure_policy = "continue"`` — banner I/O is best-effort. The opt-out
hint on the third dim line is the discoverability surface for
``AI_HATS_NO_UPDATE_CHECK``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

from ...update_check import (
    OPT_OUT_ENV,
    is_disabled,
    upstream_update,
)
from ...update_check.cache import CacheEntry
from ..step import Step, StepIO


_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _short(sha: str) -> str:
    return sha[:7] if sha else "?"


def _render(entry: CacheEntry) -> str:
    """Three-line banner. Prefer ``git describe`` labels; in the SHA-fallback
    branch append ``, +<behind> commits`` so the user sees the delta size
    without a human-readable tag.
    """
    installed_label = entry.installed_label or _short(entry.installed_sha)
    latest_label = entry.latest_label or _short(entry.latest_sha)
    have_labels = entry.installed_label is not None and entry.latest_label is not None
    if have_labels:
        delta_suffix = ""
    else:
        # ``has_update`` guarantees ``behind`` is a positive int by the time
        # we render, but stay defensive so an unexpected schema never raises
        # on the session-end hot path.
        n = entry.behind if isinstance(entry.behind, int) and entry.behind > 0 else 0
        delta_suffix = f", +{n} commits" if n else ""
    return (
        f"\n{_YELLOW}ai-hats update available "
        f"({installed_label} → {latest_label}{delta_suffix}){_RESET}\n"
        f"  run: {_CYAN}ai-hats self update{_RESET}\n"
        f"  {_DIM}silence: export {OPT_OUT_ENV}=1{_RESET}\n"
    )


class RenderUpdateBanner(Step):
    failure_policy = "continue"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="render_update_banner",
            requires=frozenset({"project_dir"}),
        )

    def run(self, *, project_dir: Path, **_: Any) -> dict[str, Any]:
        if is_disabled():
            return {}
        # The behind-upstream predicate (LOCAL channel + has_update + running-SHA
        # match) is centralized in ``upstream_update`` (HATS-846), shared with hook
        # self-heal so the guard set can't diverge per-consumer. ``is_disabled`` is
        # the banner's own UI opt-out and stays here, above the predicate.
        entry = upstream_update(project_dir)
        if entry is None:
            return {}
        sys.stderr.write(_render(entry))
        sys.stderr.flush()
        return {}
