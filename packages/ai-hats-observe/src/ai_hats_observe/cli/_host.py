"""The host the session-browse CLI runs under.

Standalone — only ai-hats-core installed — the commands run under ``STANDALONE``:
project-local, worktree-free resolvers. An integrator attaches its own ``Host``
once; readers ask ``host()`` at call time, so one ``attach`` reaches every
command. Nothing here is assigned from outside: ``attach`` is the only writer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ai_hats_core.layout import ForeignPinPolicy, ProjectLayout, resolve_root
from rich.console import Console

ProviderAdapter = Callable[[str], tuple[object | None, object | None]]


def _default_layout(start: Path | None = None) -> ProjectLayout:
    """Standalone geometry: the shared resolver, base at the flat ``.agent``.

    An integrator's host brings its AI_HATS_DIR/yaml-aware layout (base
    ``.agent/ai-hats``); standalone keeps the deliberate flat tree —
    ``.agent/sessions/runs`` — same rationale as the tracker's standalone dirs.
    """
    where = start if start is not None else Path.cwd()
    root = resolve_root(where, os.environ, on_foreign_pin=ForeignPinPolicy.WARN_AND_IGNORE)
    return ProjectLayout(root=root, base=root / ".agent")


def _default_tag_filter_parser(raw: Iterable[str]) -> dict[str, str]:
    """Minimal wt-free ``k=v`` tag-filter parser for standalone ``list --tag``.

    Splits each ``key=value`` on the first ``=``; raises ``ValueError`` on a
    missing ``=`` or empty key. ``ValueError`` is the shared contract the command
    catches (the integrator's ``TagValidationError`` subclasses it).
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

    Returns ``(transcript_resolver, parser)``; ``(None, None)`` makes backfill
    report "no transcript" instead of guessing a path layout.
    """
    del provider
    return None, None


@dataclass(frozen=True)
class Host:
    """The four things a host provides — as ONE value, so a partial override cannot happen."""

    layout: Callable[[], ProjectLayout]
    tag_filter_parser: Callable[[Iterable[str]], dict[str, str]]
    provider_adapter: ProviderAdapter
    console: Console


STANDALONE = Host(
    layout=_default_layout,
    tag_filter_parser=_default_tag_filter_parser,
    provider_adapter=_default_provider_adapter,
    console=Console(),
)

_current: Host = STANDALONE


def attach(host: Host) -> Host:
    """Run under ``host`` from now on; returns the one it replaced so a test can put it back."""
    global _current
    previous, _current = _current, host
    return previous


def host() -> Host:
    return _current
