"""ADR-0023's tables render from code and go red when the document falls behind."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "scripts" / "gen_gate_table.py"
DOC = REPO_ROOT / "docs" / "adr" / "0023-quality-gate.md"

SKELETON = (
    "# a doc\n\nprose above\n\n<!-- gate-table:stages -->\nold\n<!-- /gate-table:stages -->\n\n"
    "more prose\n\n<!-- gate-table:gates -->\nold\n<!-- /gate-table:gates -->\n\nprose below\n"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, str(GENERATOR), *args], capture_output=True, text=True, check=False
    )


def test_write_then_check_is_green_and_the_prose_survives(tmp_path: Path):
    doc = tmp_path / "adr.md"
    doc.write_text(SKELETON, encoding="utf-8")

    assert _run("--write", "--doc", str(doc)).returncode == 0
    checked = _run("--check", "--doc", str(doc))

    assert checked.returncode == 0, checked.stderr
    text = doc.read_text(encoding="utf-8")
    for prose in ("prose above", "more prose", "prose below"):
        assert prose in text
    assert "old" not in text.split("<!-- gate-table:stages -->")[1].split("<!--")[0]


def test_the_rendered_tables_carry_every_stage_and_every_gate(tmp_path: Path):
    doc = tmp_path / "adr.md"
    doc.write_text(SKELETON, encoding="utf-8")
    _run("--write", "--doc", str(doc))
    text = doc.read_text(encoding="utf-8")

    rows = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(REPO_ROOT / "scripts" / "gates.sh"), "table"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for row in rows:
        stage = row.split("|", 1)[0].strip()
        assert f"`{stage}`" in text, f"the stage table lost {stage}"
    for gate in ("review-gate", "merge-gate", "done-gate", "push-gate"):
        assert f"`{gate}`" in text
    assert "`rack.tasks`: `->done`" in text, "where done-gate applies comes from the role"
    assert "`wt`: `pre-merge`" in text
    assert "`git pre-push`" in text, "where push-gate applies comes from the skill's git_hooks"


def test_a_stale_document_is_refused_naming_the_remedy(tmp_path: Path):
    """Positive control for the check: a document that lost a row goes red."""
    doc = tmp_path / "adr.md"
    doc.write_text(SKELETON, encoding="utf-8")
    _run("--write", "--doc", str(doc))
    text = doc.read_text(encoding="utf-8")
    stale = "\n".join(line for line in text.splitlines() if "`unit`" not in line) + "\n"
    assert stale != text, "the control must actually remove something"
    doc.write_text(stale, encoding="utf-8")

    checked = _run("--check", "--doc", str(doc))

    assert checked.returncode == 1
    assert "is stale" in checked.stderr
    assert "--write" in checked.stderr


def test_a_document_without_the_markers_is_broken_not_stale(tmp_path: Path):
    doc = tmp_path / "adr.md"
    doc.write_text("# no markers here\n", encoding="utf-8")

    checked = _run("--check", "--doc", str(doc))

    assert checked.returncode == 3
    assert "BROKEN" in checked.stderr


@pytest.mark.parametrize("mode", ["--check"])
def test_the_real_adr_is_current(mode: str):
    """The stage this test stands behind: the shipped ADR matches the code."""
    checked = _run(mode)

    assert checked.returncode == 0, checked.stderr
    assert DOC.exists()
