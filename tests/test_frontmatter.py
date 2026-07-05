"""Unit tests for the real YAML frontmatter parser (HATS-813).

Replaces the two line-scanners (``providers._extract_frontmatter_description``
and ``migration_v07._skill_description``). The parser must read arbitrarily
nested frontmatter (the top-level ``ai_hats.*`` shape HATS-814 consumes) and
fail **loud** on a malformed block — the Claude Code harness drops a bad
frontmatter block silently and totally (HATS-812 PoC finding #4), so ai-hats'
own parser raises instead.
"""

from __future__ import annotations

import pytest

from ai_hats.constants import HOOK_PRE_TOOL_USE
from ai_hats.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
    read_frontmatter,
)


def test_parse_nested_metadata_block():
    """A nested ``metadata:`` dict survives the parse (line-scanner could not)."""
    text = (
        "---\n"
        "name: secret-guard\n"
        "description: guards every Bash call\n"
        "metadata:\n"
        "  ai_hats:\n"
        "    runtime_hooks:\n"
        "      PreToolUse:\n"
        "        - matcher: Bash\n"
        "          script: hooks/guard.sh\n"
        "---\n"
        "# body\n"
    )
    data = parse_frontmatter(text)
    assert data["description"] == "guards every Bash call"
    hooks = data["metadata"]["ai_hats"]["runtime_hooks"][HOOK_PRE_TOOL_USE]
    assert hooks == [{"matcher": "Bash", "script": "hooks/guard.sh"}]


def test_malformed_block_raises_loud():
    """Invalid YAML inside the fence is a loud error, not a silent empty dict."""
    text = "---\ndescription: ok\n  bad: : indent\n---\nbody\n"
    with pytest.raises(FrontmatterError):
        parse_frontmatter(text)


def test_non_mapping_body_raises_loud():
    """A fenced block that parses to a non-mapping (list/scalar) is loud."""
    text = "---\n- a\n- b\n---\nbody\n"
    with pytest.raises(FrontmatterError):
        parse_frontmatter(text)


def test_absent_fence_returns_empty():
    """A document with no leading fence is empty metadata, not an error."""
    assert parse_frontmatter("# just a heading\n") == {}


def test_unterminated_fence_returns_empty():
    """A missing closing fence yields no usable frontmatter (caller falls back)."""
    assert parse_frontmatter("---\ndescription: x\nbody without close\n") == {}


def test_empty_block_returns_empty():
    """An empty frontmatter block parses to ``{}`` (YAML null), not an error."""
    assert parse_frontmatter("---\n---\nbody\n") == {}


def test_quoted_description_is_unquoted_by_yaml():
    """A quoted value comes back unquoted — the line-scanner stripped quotes by
    hand; the real parser gets it for free."""
    assert parse_frontmatter('---\ndescription: "alpha skill"\n---\n')[
        "description"
    ] == "alpha skill"


def test_read_frontmatter_missing_file_returns_empty(tmp_path):
    assert read_frontmatter(tmp_path / "nope" / "SKILL.md") == {}


def test_read_frontmatter_reads_file(tmp_path):
    md = tmp_path / "SKILL.md"
    md.write_text("---\ndescription: from disk\n---\nbody\n")
    assert read_frontmatter(md)["description"] == "from disk"
