"""The ticket-ids checker, on a corpus small enough to reason about.

Every discrimination is asserted in BOTH directions in one test: the form that
must be refused is refused and the neighbouring form that must survive survives.
A one-directional assertion cannot tell a working checker from one that flags
everything — and the placeholder/id split is the whole design, so getting it
wrong in one direction breaks the CLI templates the library teaches.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_no_ticket_ids as ticket_ids  # noqa: E402

LIB = ticket_ids.LIBRARY_RELPATH
MARKER = ticket_ids.OPT_OUT


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature tree: a library with one skill, and the control corpus."""
    root = tmp_path / "repo"
    (root / LIB / "core" / "skills" / "demo").mkdir(parents=True)
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-x.md").write_text("Decided in HATS-1.\n")
    return root


def _skill(root: Path, body: str, name: str = "SKILL.md") -> Path:
    path = root / LIB / "core" / "skills" / "demo" / name
    path.write_text(body)
    return path


def _run(root: Path) -> int:
    return ticket_ids.main([str(root)])


def test_a_concrete_id_is_refused_and_a_placeholder_is_not(repo: Path, capsys):
    """The one discrimination the whole gate rests on.

    `HATS-NNN` teaches the shape of an id in a CLI template; `HATS-1430` cites
    history. Digits are the only thing separating them, so both live on the same
    line here — a checker that judged the line rather than the token would have
    to fail one of the two assertions.
    """
    _skill(repo, "Run `rack ls HATS-NNN` — the form landed in HATS-1430.\n")
    assert _run(repo) == 1
    fails = [line for line in capsys.readouterr().err.splitlines() if "FAIL" in line]
    assert len(fails) == 1, fails
    assert "HATS-1430" in fails[0] and "HATS-NNN" not in fails[0], fails


def test_an_adr_citation_is_not_a_ticket_id(repo: Path, capsys):
    _skill(repo, "See ADR-0005 §D2 and ADR-0007.\n")
    assert _run(repo) == 0
    assert "FAIL" not in capsys.readouterr().err


def test_the_marker_allows_the_line_and_records_the_reason(repo: Path, capsys):
    """An id a machine PRINTS keeps its place — but has to say why."""
    _skill(repo, f"a wall of HATS-1242 errors  <!-- {MARKER} the guard prints it -->\n")
    assert _run(repo) == 0
    out = capsys.readouterr().err
    assert "allowed" in out
    assert "the guard prints it" in out


def test_the_marker_without_a_reason_is_named_as_such(repo: Path, capsys):
    _skill(repo, f"a wall of HATS-1242 errors  <!-- {MARKER} -->\n")
    assert _run(repo) == 0
    assert "(no reason given)" in capsys.readouterr().err


def test_the_marker_does_not_leak_to_the_next_line(repo: Path, capsys):
    """Line-scoped, not file-scoped: one blessed id must not bless the file."""
    _skill(repo, f"printed HATS-1242  <!-- {MARKER} guard -->\nbut HATS-1430 is history.\n")
    assert _run(repo) == 1
    fails = [line for line in capsys.readouterr().err.splitlines() if "FAIL" in line]
    assert len(fails) == 1, fails
    assert "HATS-1430" in fails[0] and ":2:" in fails[0], fails


def test_hooks_and_git_hooks_are_out_of_the_corpus(repo: Path):
    """Two distinct segments — a single `hooks` glob does not match `git_hooks`."""
    for segment in ("hooks", "git_hooks"):
        directory = repo / LIB / "core" / "skills" / "demo" / segment
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "note.md").write_text("carried over from HATS-1430.\n")
    assert _run(repo) == 0


def test_library_code_is_out_of_the_corpus(repo: Path):
    """Removing an id from code is a rewrite, not a deletion — a separate card."""
    demo = repo / LIB / "core" / "skills" / "demo"
    (demo / "gate.sh").write_text("# HATS-1430\n")
    (demo / "helper.py").write_text("# HATS-1430\n")
    assert _run(repo) == 0


def test_a_dead_pattern_fails_even_on_a_clean_corpus(repo: Path, capsys):
    """The gate's own positive control, and the reason it is not decoration.

    A checker whose pattern stopped matching reports a clean tree in exactly the
    words a genuinely clean tree earns. Here the corpus IS clean and the control
    corpus is present but yields nothing, so the run must refuse rather than
    congratulate.
    """
    _skill(repo, "No ids here.\n")
    (repo / "docs" / "adr" / "0001-x.md").write_text("No ids here either.\n")
    assert _run(repo) == 1
    out = capsys.readouterr().err
    assert "BROKEN" in out
    assert "FAIL" not in out


def test_an_absent_control_corpus_is_skipped_not_failed(tmp_path: Path, capsys):
    """A scratch tree has no CHANGELOG; absence is not the same as a dead pattern."""
    root = tmp_path / "bare"
    (root / LIB / "core" / "skills" / "demo").mkdir(parents=True)
    (root / LIB / "core" / "skills" / "demo" / "SKILL.md").write_text("clean\n")
    assert ticket_ids.main([str(root)]) == 0
    assert "positive control: skipped" in capsys.readouterr().err


def test_the_live_library_is_clean(capsys):
    """The tree this repository actually ships, not a fixture."""
    assert ticket_ids.main([]) == 0
    assert "positive control: the pattern still finds" in capsys.readouterr().err
