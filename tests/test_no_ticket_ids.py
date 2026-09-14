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


def test_library_code_is_out_and_this_repo_s_own_code_is_in(repo: Path, capsys):
    """The two `.py` files differ only in which tree they sit in.

    The library's installs into other people's projects, so sweeping it changes
    shipped behaviour; `src/` is read only by someone standing in this
    repository, for whom `git log -S` resolves what the id cannot. Asserted in
    one test because a corpus that judged `.py` by suffix would fail one half.
    """
    demo = repo / LIB / "core" / "skills" / "demo"
    (demo / "gate.sh").write_text("# HATS-1430\n")
    (demo / "helper.py").write_text("# HATS-1430\n")
    _code(repo, "# HATS-1651: why this branch exists.\n")
    assert _run(repo) == 1
    fails = [line for line in capsys.readouterr().err.splitlines() if "FAIL" in line]
    assert len(fails) == 1, fails
    assert "src/ai_hats/thing.py:1" in fails[0] and "HATS-1651" in fails[0], fails


def test_a_docstring_is_judged_like_the_prose_it_is(repo: Path, capsys):
    """Four fifths of the code surface was docstrings, not comments."""
    _skill(repo, "clean\n")
    _code(repo, '"""Seed the layout (HATS-469)."""\n')
    assert _run(repo) == 1
    assert "src/ai_hats/thing.py:1" in capsys.readouterr().err


def test_pycache_is_not_part_of_the_code_corpus(repo: Path):
    """A stale `.pyc` neighbour is a build artifact, not a file anyone reads."""
    _skill(repo, "clean\n")
    cached = repo / "src" / "ai_hats" / "__pycache__"
    cached.mkdir(parents=True)
    (cached / "stale.py").write_text("# HATS-1430\n")
    assert _run(repo) == 0


def test_a_todo_keeps_its_id_and_does_not_shield_the_line(repo: Path, capsys):
    """The one blessed form, and the reason masking beats skipping the line.

    `TODO(<id>)` points FORWARD at work no commit holds yet, so `git log -S`
    cannot find it and the id is the only pointer there is. A checker that
    skipped the whole LINE instead of masking the form would let the provenance
    id beside it ride along — which is why both sit on one line here.
    """
    _skill(repo, "clean\n")
    _code(repo, "# TODO(HATS-1785): real type. Shape settled in HATS-1430.\n")
    assert _run(repo) == 1
    err = capsys.readouterr().err
    fails = [line for line in err.splitlines() if "FAIL" in line]
    assert len(fails) == 1, fails
    assert "HATS-1430" in fails[0] and "HATS-1785" not in fails[0], fails
    assert "spared: 1 `TODO(HATS-<id>)` site" in err, err


def test_a_todo_suffixed_id_is_spared_without_being_counted(repo: Path, capsys):
    """`HATS-120b` is blessed too, but was never at risk.

    `\\b` after the digits needs a non-word character and finds a letter, so the
    id pattern never matched it. Counting it as spared would overstate what the
    carve-out does — the tally has to name sites the gate would otherwise flag.
    """
    _skill(repo, "clean\n")
    _code(repo, "# TODO(HATS-120b): drop once Click 9 is pinned.\n")
    assert _run(repo) == 0
    assert "spared" not in capsys.readouterr().err


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


def _code(root: Path, body: str, name: str = "thing.py") -> Path:
    path = root / "src" / "ai_hats" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _doc(root: Path, body: str, name: str = "how-to.md") -> Path:
    path = root / "docs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_a_living_doc_is_judged_and_a_dated_record_is_not(repo: Path, capsys):
    """`.agent/` is gitignored, so a doc id is a dead link on a fresh clone too.

    The record is the other half of the same decision: an ADR names the id it
    decided, and correcting that would rewrite history rather than a reference.
    """
    _skill(repo, "clean\n")
    _doc(repo, "The plan home moved in HATS-637.\n")
    (repo / "docs" / "adr" / "0002-y.md").write_text("Decided in HATS-1430.\n")
    (repo / "docs" / "migration-v0.9.0.md").write_text("Renamed in HATS-1430.\n")
    assert _run(repo) == 1
    fails = [line for line in capsys.readouterr().err.splitlines() if "FAIL" in line]
    assert len(fails) == 1, fails
    assert "docs/how-to.md:1" in fails[0] and "HATS-637" in fails[0], fails


def test_the_root_docs_are_judged(repo: Path, capsys):
    _skill(repo, "clean\n")
    (repo / "CONTRIBUTING.md").write_text("The scope split landed in HATS-1651.\n")
    assert _run(repo) == 1
    assert "CONTRIBUTING.md:1" in capsys.readouterr().err


def test_a_fenced_sample_is_not_a_citation(repo: Path, capsys):
    """The discrimination the docs half rests on, asserted both ways at once.

    `rack transition HATS-042 …` teaches the grammar of a link and needs two
    distinguishable numbers to do it; the same token in a sentence cites history.
    """
    _skill(repo, "clean\n")
    _doc(
        repo,
        "```bash\nrack transition HATS-042 --link depends_on:HATS-041\n```\n\nLanded in HATS-1430.\n",
    )
    assert _run(repo) == 1
    fails = [line for line in capsys.readouterr().err.splitlines() if "FAIL" in line]
    assert len(fails) == 1, fails
    assert "HATS-1430" in fails[0] and "HATS-042" not in fails[0], fails


def test_a_nested_fence_does_not_flip_the_parity(repo: Path, capsys):
    """A ```` block holding ``` ones is two fences, not three.

    Counting every ```-prefixed line as a toggle leaves the file "open" at the
    end, and every line after it stops being judged — silently, which is how a
    dead reference sat on the last line of a doc the gate was supposed to read.
    """
    _skill(repo, "clean\n")
    _doc(repo, "````markdown\n```bash\nrack ls HATS-092\n```\n````\n\nLanded in HATS-1430.\n")
    assert _run(repo) == 1
    fails = [line for line in capsys.readouterr().err.splitlines() if "FAIL" in line]
    assert len(fails) == 1, fails
    assert "HATS-1430" in fails[0] and ":7:" in fails[0], fails


def test_an_unclosed_fence_is_reported_rather_than_swallowed(repo: Path, capsys):
    """A gate that cannot see part of a file has to say which part."""
    _skill(repo, "clean\n")
    _doc(repo, "```bash\nrack ls\n\nLanded in HATS-1430.\n")
    assert _run(repo) == 0
    out = capsys.readouterr().err
    assert "blind — docs/how-to.md:1" in out, out
    assert "FAIL" not in out


def test_the_dated_records_match_the_sibling_gate():
    """Both gates exempt the same set; neither imports the other to do it."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import check_prose_refs  # noqa: PLC0415

    assert ticket_ids.DATED_RECORD_RE.pattern == check_prose_refs.DATED_RECORD_RE.pattern


def test_the_live_tree_is_clean(capsys):
    """The tree this repository actually ships, not a fixture."""
    assert ticket_ids.main([]) == 0
    out = capsys.readouterr().err
    assert "positive control: the pattern still finds" in out
    assert "files (library prose + living docs + src/)" in out
