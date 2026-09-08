"""The consumer-refs checker, on a corpus small enough to reason about.

The invariant is directional, so every test asserts BOTH directions: the
reference that must be refused is refused, and the one that must survive
survives. A one-directional assertion cannot tell a working checker from one
that flags every occurrence of a component name — and this checker's whole
design is that a role naming its trait is fine while a trait naming its role is
not.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_consumer_refs as consumer_refs  # noqa: E402

LIB = consumer_refs.LIBRARY_RELPATH
MARKER = consumer_refs.OPT_OUT


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature library: one role composing one trait, that trait's skill."""
    root = tmp_path / "repo"
    for rel in ("core/roles/pilot", "core/traits/gear", "core/skills/wrench"):
        (root / LIB / rel).mkdir(parents=True)
    _write(
        root,
        "core/roles/pilot/config.yaml",
        "name: pilot\ncomposition:\n  traits:\n    - gear\n",
    )
    _write(
        root,
        "core/traits/gear/config.yaml",
        "name: gear\ncomposition:\n  skills:\n    - wrench\n",
    )
    _write(root, "core/skills/wrench/SKILL.md", "# wrench\n\nTighten things.\n")
    return root


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / LIB / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _run(root: Path) -> int:
    return consumer_refs.main([str(root)])


def _fails(capsys) -> list[str]:
    return [line for line in capsys.readouterr().err.splitlines() if "FAIL" in line]


def test_a_trait_comment_names_its_role_but_not_a_stranger(repo: Path, capsys):
    """The one discrimination the whole gate rests on.

    Both names sit in the same file, so a checker that judged "is this a role
    name" rather than "does this role compose me" would have to fail one of the
    two assertions.
    """
    _write(
        repo,
        "core/roles/rigger/config.yaml",
        "name: rigger\ncomposition:\n  traits: []\n",
    )
    _write(
        repo,
        "core/traits/gear/config.yaml",
        "name: gear\ncomposition:\n"
        "  # pilot needs this, rigger does not\n"
        "  skills:\n    - wrench\n",
    )
    assert _run(repo) == 1
    fails = _fails(capsys)
    assert len(fails) == 1, fails
    assert "pilot" in fails[0] and "rigger" not in fails[0], fails


def test_a_role_may_name_the_trait_it_composes(repo: Path, capsys):
    """The asymmetry, and it falls out of the graph: a role has no carriers."""
    _write(
        repo,
        "core/roles/pilot/config.yaml",
        "name: pilot\ncomposition:\n  traits:\n    # gear carries the wrench\n    - gear\n",
    )
    assert _run(repo) == 0
    assert "FAIL" not in capsys.readouterr().err


def test_shipped_prose_needs_a_relationship_claim_not_a_bare_name(repo: Path, capsys):
    """A skill addressing its own role is a contract; naming its carrier is not.

    Both lines name `pilot`, which composes the skill through `gear`. Only the
    provenance claim is a finding — otherwise every role-specific protocol skill
    would be unwritable.
    """
    _write(
        repo,
        "core/skills/wrench/SKILL.md",
        "# wrench\n\nYou were launched as `pilot` with a bolt to turn.\n"
        "\n## Who gets it\n\nCarried by the `pilot` role.\n",
    )
    assert _run(repo) == 1
    fails = _fails(capsys)
    assert len(fails) == 1, fails
    assert "SKILL.md:7:" in fails[0], fails  # the provenance line, not line 3


def test_a_yaml_comment_needs_no_relationship_claim(repo: Path, capsys):
    """The surfaces differ in strictness on purpose.

    This bare mention has no `carried by` to match. In shipped prose it would
    survive; in an authoring comment, which addresses no reader, it does not.
    """
    _write(
        repo,
        "core/traits/gear/config.yaml",
        "name: gear\ncomposition:\n"
        "  # the wrench also lands in pilot's toolbox\n"
        "  skills:\n    - wrench\n",
    )
    assert _run(repo) == 1
    assert len(_fails(capsys)) == 1


def test_block_scalar_content_is_not_a_yaml_comment(repo: Path, capsys):
    """`## PILOT` inside `injection: |` is a markdown heading, not a comment."""
    _write(
        repo,
        "core/traits/gear/config.yaml",
        "name: gear\ncomposition:\n  skills:\n    - wrench\n"
        "injection: |\n  ## GEAR\n\n  # pilot reads this, and that is the point\n",
    )
    assert _run(repo) == 0
    assert "FAIL" not in capsys.readouterr().err


def test_a_trailing_comment_is_a_comment_and_a_quoted_hash_is_not(repo: Path, capsys):
    """Where a violation actually gets typed: at the end of the list item."""
    _write(
        repo,
        "core/traits/gear/config.yaml",
        "name: gear\ncomposition:\n  skills:\n"
        "    - wrench  # pilot needs it\n"
        "  note: 'sharp # not a comment, and pilot is inside the quotes'\n",
    )
    assert _run(repo) == 1
    fails = _fails(capsys)
    assert len(fails) == 1, fails
    assert ":4" in fails[0], fails


def test_the_marker_allows_the_line_and_records_the_reason(repo: Path, capsys):
    _write(
        repo,
        "core/traits/gear/config.yaml",
        f"name: gear\ncomposition:\n"
        f"  # pilot is the one caller  # {MARKER} the trait ships nowhere else\n"
        f"  skills:\n    - wrench\n",
    )
    assert _run(repo) == 0
    err = capsys.readouterr().err
    assert "allowed" in err and "ships nowhere else" in err


def test_a_marker_without_a_reason_says_so(repo: Path, capsys):
    _write(
        repo,
        "core/traits/gear/config.yaml",
        f"name: gear\ncomposition:\n  # pilot  # {MARKER}\n  skills:\n    - wrench\n",
    )
    assert _run(repo) == 0
    assert "(no reason given)" in capsys.readouterr().err


def test_the_control_finds_its_own_sample(repo: Path):
    """One detector each, on a sample the checker builds and scans itself."""
    assert consumer_refs.control_hits() == 2


def test_a_clean_run_is_refused_when_the_control_comes_back_short(repo: Path, capsys):
    """A green verdict over a clean corpus proves nothing about a dead pattern."""
    assert consumer_refs.main([str(repo)], control=lambda: 0) == 1
    assert "BROKEN" in capsys.readouterr().err


def test_the_control_does_not_make_a_clean_library_fail(repo: Path, capsys):
    assert _run(repo) == 0
    err = capsys.readouterr().err
    assert "positive control" in err
    assert "BROKEN" not in err


def test_a_carrier_reached_through_a_trait_still_counts(repo: Path, capsys):
    """`pilot` composes `gear`, `gear` composes `wrench` — so `pilot` carries it."""
    _write(
        repo,
        "core/skills/wrench/SKILL.md",
        "# wrench\n\nInstalled via `gear` — carried by the `pilot` role.\n",
    )
    assert _run(repo) == 1
    fails = _fails(capsys)
    assert len(fails) == 2, fails  # one line, two upward names: the trait and the role
    assert "`gear`" in fails[0] and "`pilot`" in fails[1], fails


def test_the_script_refuses_from_the_command_line(repo: Path):
    """The argv path and the exit code, which an in-process `main()` never proves."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_consumer_refs.py"
    _write(
        repo,
        "core/traits/gear/config.yaml",
        "name: gear\ncomposition:\n  # pilot needs this\n  skills:\n    - wrench\n",
    )
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, str(script), str(repo)], capture_output=True, text=True, check=False
    )
    assert out.returncode == 1, out.stderr
    assert "FAIL" in out.stderr and "pilot" in out.stderr
