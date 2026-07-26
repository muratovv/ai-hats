"""Migration step 7 — drop the orphaned root CLAUDE.md scaffold (HATS-1201).

HATS-1170 stopped writing the root ``CLAUDE.md`` scaffold and killed the only
code that ever cleaned one up, leaving every upgraded project with an
ai-hats-authored block that nothing owns. This migration removes our own
leftover; user content around it is preserved verbatim.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.constants import (
    INJECTION_END,
    INJECTION_START,
    PUBLISH_AGGREGATOR_END,
    PUBLISH_AGGREGATOR_START,
)
from ai_hats.migrations import _m_strip_orphaned_claude_scaffold
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

SCAFFOLD = f"{PUBLISH_AGGREGATOR_START}\n@./.agent/ai-hats/imports.md\n{PUBLISH_AGGREGATOR_END}\n"


def _project(tmp_path: Path, claude_md: str | None = None) -> Assembler:
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(provider="claude").save(project / PROJECT_CONFIG)
    if claude_md is not None:
        (project / "CLAUDE.md").write_text(claude_md)
    return Assembler(project)


def test_pure_scaffold_file_is_removed(tmp_path: Path) -> None:
    """A CLAUDE.md that is nothing but our block was never user content."""
    asm = _project(tmp_path, SCAFFOLD)

    _m_strip_orphaned_claude_scaffold(asm)

    assert not (asm.project_dir / "CLAUDE.md").exists()


def test_user_content_below_the_block_survives(tmp_path: Path) -> None:
    asm = _project(tmp_path, SCAFFOLD + "\n# My project\n\nHand-written notes.\n")

    _m_strip_orphaned_claude_scaffold(asm)

    assert (asm.project_dir / "CLAUDE.md").read_text() == "# My project\n\nHand-written notes.\n"


def test_user_content_around_the_block_survives(tmp_path: Path) -> None:
    asm = _project(tmp_path, "# Top matter\n\n" + SCAFFOLD + "\n## Tail\n")

    _m_strip_orphaned_claude_scaffold(asm)

    assert (asm.project_dir / "CLAUDE.md").read_text() == "# Top matter\n\n## Tail\n"


def test_legacy_uppercase_injection_block_is_removed(tmp_path: Path) -> None:
    """Pre-v3 projects carry the inline injection block, not the aggregator."""
    legacy = f"{INJECTION_START}\nstale role text\n{INJECTION_END}\n"
    asm = _project(tmp_path, legacy + "\n# Mine\n")

    _m_strip_orphaned_claude_scaffold(asm)

    assert (asm.project_dir / "CLAUDE.md").read_text() == "# Mine\n"


def test_user_file_without_markers_is_untouched(tmp_path: Path) -> None:
    body = "# Just mine\n\nNo ai-hats here.\n"
    asm = _project(tmp_path, body)

    _m_strip_orphaned_claude_scaffold(asm)

    assert (asm.project_dir / "CLAUDE.md").read_text() == body


def test_absent_file_is_a_noop(tmp_path: Path) -> None:
    asm = _project(tmp_path)

    _m_strip_orphaned_claude_scaffold(asm)

    assert not (asm.project_dir / "CLAUDE.md").exists()


def test_scaffold_dropped_even_when_user_rules_exist(tmp_path: Path) -> None:
    """HATS-1203 inverts HATS-1201's gate: the composed prompt carries
    user-rules now, so a non-empty imports.md no longer keeps the block alive.
    The rules themselves stay — only the orphaned block goes."""
    asm = _project(tmp_path, SCAFFOLD)
    canonical = asm.project_dir / ".agent" / "ai-hats"
    (canonical / "user-rules").mkdir(parents=True)
    (canonical / "user-rules" / "mine.md").write_text("# Mine\n")
    (canonical / "imports.md").write_text("@./user-rules/mine.md\n")

    _m_strip_orphaned_claude_scaffold(asm)

    assert not (asm.project_dir / "CLAUDE.md").exists()
    assert (canonical / "user-rules" / "mine.md").read_text() == "# Mine\n"


def test_empty_aggregator_does_not_block_the_drop(tmp_path: Path) -> None:
    """An imports.md with no user-rules carries nothing — strip as usual."""
    asm = _project(tmp_path, SCAFFOLD)
    canonical = asm.project_dir / ".agent" / "ai-hats"
    canonical.mkdir(parents=True)
    (canonical / "imports.md").write_text("")

    _m_strip_orphaned_claude_scaffold(asm)

    assert not (asm.project_dir / "CLAUDE.md").exists()


def test_second_run_is_a_noop(tmp_path: Path) -> None:
    """Migration contract: idempotent under replay and concurrent refreshes."""
    asm = _project(tmp_path, SCAFFOLD + "\n# Mine\n")

    _m_strip_orphaned_claude_scaffold(asm)
    after_first = (asm.project_dir / "CLAUDE.md").read_text()
    _m_strip_orphaned_claude_scaffold(asm)

    assert (asm.project_dir / "CLAUDE.md").read_text() == after_first
