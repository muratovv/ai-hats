"""The skill-desc checker, on documents small enough to see the spelling.

A pass and a refusal travel the same judge. One direction cannot tell a
checker that flags nothing from one that flags everything.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_skill_desc as skill_desc  # noqa: E402

LIB = skill_desc.LIBRARY_RELPATH


def _doc(description: str) -> str:
    return f"---\nname: demo\n{description}\nlicense: MIT\n---\n# body\n"


def test_control_samples_classify_both_ways():
    assert skill_desc.control_ok()


@pytest.mark.parametrize(
    "description",
    [
        'description: "quoted"',
        "description: plain",
        "description: 'single'",
        "description: >\n  clip keeps a newline",
        "description: >+\n  keep",
        "description: >-\n  one\n\n  two",
        "description: >-\n  see `a\n  b`",
        "description: |\n  literal",
    ],
)
def test_other_spellings_are_refused(description: str):
    assert skill_desc.judge(_doc(description))


def test_a_wrapped_strip_with_one_parsed_line_passes():
    text = _doc("description: >-\n  one line that\n  wraps in the file")
    assert skill_desc.judge(text) == []


def test_a_missing_description_is_refused():
    assert skill_desc.judge("---\nname: demo\n---\n")


def test_main_names_the_bad_file_and_not_the_good_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    root = tmp_path / "repo"
    bad = root / LIB / "core" / "skills" / "bad"
    good = root / LIB / "core" / "skills" / "good"
    bad.mkdir(parents=True)
    good.mkdir(parents=True)
    (bad / "SKILL.md").write_text(_doc('description: "nope"'), encoding="utf-8")
    (good / "SKILL.md").write_text(_doc("description: >-\n  fine"), encoding="utf-8")

    assert skill_desc.main([str(root)]) == 1
    err = capsys.readouterr().err
    assert "skills/bad/SKILL.md" in err
    assert "skills/good/SKILL.md" not in err
    assert "not covered:" in err


def test_main_is_quiet_on_a_folded_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    root = tmp_path / "repo"
    skill = root / LIB / "usage" / "skills" / "ok"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(_doc("description: >-\n  fine"), encoding="utf-8")

    assert skill_desc.main([str(root)]) == 0
    err = capsys.readouterr().err
    assert "FAIL" not in err
    assert "positive control:" in err


def test_an_empty_library_is_not_a_clean_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    root = tmp_path / "repo"
    (root / LIB).mkdir(parents=True)

    assert skill_desc.main([str(root)]) == 1
    assert "BROKEN" in capsys.readouterr().err
