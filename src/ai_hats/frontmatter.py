"""Real YAML frontmatter parser for SKILL.md (HATS-813).

A single primitive that reads a Markdown document's leading ``---`` fenced
YAML block into a mapping — including arbitrarily nested keys (e.g.
``metadata.ai_hats.*``, the shape HATS-814 consumes). It replaces two
line-scanners (``providers._extract_frontmatter_description`` and
``migration_v07._skill_description``) that could only read a flat
``description:`` line.

Loudness contract: where a fenced block is present but is not valid YAML — or
parses to something other than a mapping — we raise :class:`FrontmatterError`
instead of returning an empty mapping. The Claude Code harness drops a malformed
frontmatter block *silently and totally* (HATS-812 PoC finding #4); ai-hats fails
loud so a config typo surfaces at read time rather than vanishing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_FENCE = "---"


class FrontmatterError(ValueError):
    """A frontmatter fence is present but its body is not a valid YAML mapping."""


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse the leading YAML frontmatter block of ``text``.

    Returns the parsed mapping. Returns ``{}`` when there is no usable
    frontmatter fence (absent opening fence, missing closing fence, or an empty
    block) — a legitimate "no metadata" state, not an error.
    """
    if not text.startswith(_FENCE):
        return {}
    lines = text.splitlines()
    if lines[0].strip() != _FENCE:
        return {}
    closing = next(
        (i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None
    )
    if closing is None:
        return {}
    block = "\n".join(lines[1:closing])
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"invalid YAML in frontmatter: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontmatterError(
            f"frontmatter must be a mapping, got {type(data).__name__}"
        )
    return data


def read_frontmatter(path: Path) -> dict[str, Any]:
    """:func:`parse_frontmatter` on a file; ``{}`` if the file does not exist."""
    if not path.is_file():
        return {}
    return parse_frontmatter(path.read_text())
