"""`critical_target` — which `rm` operands the consent gate asks about (ADR-0039 D1).

A superset of Claude Code's own critical paths: a miss hands the removal to the
harness's classifier with nobody asked, an extra hit costs one question.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_library.hooks.consent_gate.critical_paths import critical_target


@pytest.fixture
def where(tmp_path):
    work = tmp_path / "proj" / "work"
    extra = tmp_path / "worktrees"
    home = tmp_path / "home"
    for d in (work / "build", extra / "one", home):
        d.mkdir(parents=True)
    return {"work": work, "extra": extra, "home": home}


def _judge(where, operand, *, base="work"):
    return critical_target(
        [operand],
        base=None if base is None else where[base],
        working=[where["work"]],
        home=where["home"],
        extra=[where["extra"]],
    )


CRITICAL = {
    "an unguarded variable": lambda w: "$DIR/*",
    "a guarded variable": lambda w: "${DIR:?}",
    "a command substitution": lambda w: "$(pwd)",
    "a backtick substitution": lambda w: "`pwd`",
    "the root": lambda w: "/",
    "a top-level directory": lambda w: "/usr",
    "a top-level directory, trailing slash": lambda w: "/tmp/",
    "a top-level directory, trailing dot": lambda w: "/tmp/.",
    "a glob in a top-level directory": lambda w: "/tmp/*",
    "a glob in the root": lambda w: "/U*",
    "the working directory": lambda w: str(w["work"]),
    "the working directory, trailing slash": lambda w: f"{w['work']}/",
    "the working directory, relative": lambda w: ".",
    "a glob in the working directory": lambda w: "*",
    "a relative glob in the working directory": lambda w: "./*",
    "the parent of the working directory": lambda w: "..",
    "a glob in the parent": lambda w: "../*",
    "an ancestor of the working directory": lambda w: str(w["work"].parent),
    "the home directory": lambda w: str(w["home"]),
    "the home directory by tilde": lambda w: "~",
    "an additional directory": lambda w: str(w["extra"]),
    "a glob in an additional directory": lambda w: f"{w['extra']}/*",
    "an ancestor of an additional directory": lambda w: str(w["extra"].parent),
}

ORDINARY = {
    "a relative child": lambda w: "build",
    "a dotted relative child": lambda w: "./build",
    "a glob in a child": lambda w: "build/*",
    "an absolute child": lambda w: str(w["work"] / "build"),
    "a deep temp path": lambda w: "/tmp/x/y",
    "a child of an additional directory": lambda w: str(w["extra"] / "one"),
    "a file": lambda w: "notes.txt",
}


@pytest.mark.parametrize("case", sorted(CRITICAL))
def test_a_critical_operand_is_named(where, case):
    operand = CRITICAL[case](where)
    assert _judge(where, operand) == operand


@pytest.mark.parametrize("case", sorted(ORDINARY))
def test_an_ordinary_operand_is_not(where, case):
    assert _judge(where, ORDINARY[case](where)) is None


def test_the_first_critical_operand_is_named_among_several(where):
    assert (
        critical_target(
            ["build", "..", "/usr"],
            base=where["work"],
            working=[where["work"]],
            home=where["home"],
            extra=[],
        )
        == ".."
    )


def test_a_relative_operand_with_no_known_base_is_asked_about(where):
    """`cd` to somewhere the guard cannot name: the relative target is unknowable."""
    assert _judge(where, "build", base=None) == "build"


def test_an_absolute_operand_with_no_known_base_is_still_judged(where):
    assert _judge(where, str(where["work"] / "build"), base=None) is None


def test_a_symlinked_spelling_of_the_working_directory_is_caught(where, tmp_path):
    link = tmp_path / "alias"
    link.symlink_to(where["work"])
    assert _judge(where, str(link)) == str(link)


def test_no_operands_is_no_target(where):
    assert (
        critical_target([], base=where["work"], working=[], home=Path("/nonexistent"), extra=[])
        is None
    )
