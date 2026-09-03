"""Runtime-layout injection seam for the standalone session-browse CLI (HATS-952).

The observe CLI (`session list/show/audit`) defaults to project-local, wt-free
resolvers so it runs with only ai-hats-core. The integrator overrides these
module globals at mount (`ai_hats.cli.__init__`) with its AI_HATS_DIR/yaml-aware
versions (its ``ProjectLayout``/`console`,
`ai_hats.tags.parse_tag_filters`), restoring `ai-hats session`'s exact layout and
tag semantics. Reference the slots as ``_seam.<slot>`` (attribute access at call
time) so one integrator override reaches every importer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import os

from ai_hats_core.layout import ForeignPinPolicy, ProjectLayout, resolve_root
from rich.console import Console


def _default_layout(start: Path | None = None) -> ProjectLayout:
    """Standalone geometry: the shared resolver, base at the flat ``.agent``.

    The integrator overrides the slot with its AI_HATS_DIR/yaml-aware layout
    (base ``.agent/ai-hats``); standalone keeps the deliberate flat tree —
    ``.agent/sessions/runs`` — same rationale as the tracker's standalone dirs.
    """
    where = start if start is not None else Path.cwd()
    root = resolve_root(where, os.environ, on_foreign_pin=ForeignPinPolicy.WARN_AND_IGNORE)
    return ProjectLayout(root=root, base=root / ".agent")


def _default_tag_filter_parser(raw: Iterable[str]) -> dict[str, str]:
    """Minimal wt-free ``k=v`` tag-filter parser for standalone ``list --tag``.

    Splits each ``key=value`` on the first ``=``; raises ``ValueError`` on a
    missing ``=`` or empty key. The integrator overrides with
    ``ai_hats.tags.parse_tag_filters`` (strict format / reserved-key / length
    validation). ``ValueError`` is the shared contract the command catches
    (``ai_hats.tags.TagValidationError`` subclasses it).
    """
    filters: dict[str, str] = {}
    for item in raw:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"tag filter must be key=value, got {item!r}")
        filters[key] = value
    return filters


def _default_provider_adapter(provider: str) -> tuple[object | None, object | None]:
    """Standalone: no provider registry, so ``session backfill`` reads nothing.

    Returns ``(transcript_resolver, parser)``. Discovery and parsing both belong
    to the provider (``resolve_transcript`` / ``transcript_parser``), which the
    integrator injects — HATS-948 made the writer surface-agnostic and a new
    command must not re-bake the assumption. ``(None, None)`` makes backfill
    report "no transcript" instead of guessing a path layout (HATS-1374).
    """
    del provider
    return None, None


# Injectable slots — the integrator overrides these at mount (ai_hats.cli).
_LAYOUT = _default_layout
_TAG_FILTER_PARSER = _default_tag_filter_parser
_PROVIDER_ADAPTER = _default_provider_adapter
_CONSOLE = Console()
