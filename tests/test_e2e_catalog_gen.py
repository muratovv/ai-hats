"""HATS-1498 — the parser behind the e2e flow catalog.

The catalog's source of truth is a structured block in each e2e test's module
docstring; this file pins how that block is read. A malformed block must be a
named refusal rather than a silently dropped row — a row that vanishes takes
the flow it documented with it, and the generated file still renders green.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "gen_e2e_catalog.py"


def _load():
    spec = importlib.util.spec_from_file_location("gen_e2e_catalog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load()


WELL_FORMED = '''"""e2e (HATS-788)

flow:   a maintainer closes a task from inside that task's own worktree
cmds:
    rack create A --id HATS-1
    cd <worktree>
    rack transition HATS-1 done       # must refuse
expect: the refusal exits non-zero and names "linked worktree"
why:    otherwise the close deletes the operator's cwd
"""
'''


def test_well_formed_block_parses_every_field():
    (row,) = mod.parse_rows(WELL_FORMED, "test_x.py")
    assert row.pins == ["HATS-788"]
    assert row.flow == "a maintainer closes a task from inside that task's own worktree"
    assert row.expect == 'the refusal exits non-zero and names "linked worktree"'
    assert row.why == "otherwise the close deletes the operator's cwd"


def test_cmds_keep_their_lines_and_lose_only_the_block_indent():
    (row,) = mod.parse_rows(WELL_FORMED, "test_x.py")
    assert row.cmds == [
        "rack create A --id HATS-1",
        "cd <worktree>",
        "rack transition HATS-1 done       # must refuse",
    ]


def test_a_wrapped_field_joins_into_one_sentence():
    src = WELL_FORMED.replace(
        "why:    otherwise the close deletes the operator's cwd",
        "why:    otherwise the close deletes the operator's cwd and every\n"
        "        later invocation mis-resolves the tracker",
    )
    (row,) = mod.parse_rows(src, "test_x.py")
    assert row.why == (
        "otherwise the close deletes the operator's cwd and every "
        "later invocation mis-resolves the tracker"
    )


# --- a file with no block is pending, not an error -------------------------


def test_docstring_without_a_block_is_uncatalogued():
    assert mod.parse_rows('"""just prose about HATS-1."""\n', "test_x.py") == []


def test_no_docstring_at_all_is_uncatalogued():
    assert mod.parse_rows("import os\n", "test_x.py") == []


# --- a block that exists but cannot be read is a NAMED refusal -------------


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        pytest.param(
            lambda s: s.replace("why:    otherwise the close deletes the operator's cwd\n", ""),
            "missing why",
            id="missing-field",
        ),
        pytest.param(
            lambda s: s.replace(
                'expect: the refusal exits non-zero and names "linked worktree"', "expect:"
            ),
            "empty",
            id="empty-field",
        ),
        pytest.param(
            lambda s: s.replace(
                'expect: the refusal exits non-zero and names "linked worktree"',
                'expect: one\nexpect: the refusal exits non-zero and names "linked worktree"',
            ),
            "twice",
            id="duplicate-field",
        ),
        pytest.param(
            lambda s: s.replace("e2e (HATS-788)", "some prose header"),
            "must be `e2e (HATS-NNN",
            id="bad-header",
        ),
        pytest.param(
            lambda s: s.replace("e2e (HATS-788)", "e2e (no id here)"),
            "provenance",
            id="header-without-id",
        ),
        pytest.param(
            lambda s: s.replace(
                'why:    otherwise the close deletes the operator\'s cwd\n"""',
                "why:    otherwise the close deletes the operator's cwd\n"
                '\nSome leftover prose the rewrite forgot to delete.\n"""',
            ),
            "unindented prose",
            id="trailing-prose",
        ),
    ],
)
def test_malformed_block_is_refused_by_name(mutation, expected):
    with pytest.raises(mod.CatalogError) as exc:
        mod.parse_rows(mutation(WELL_FORMED), "test_x.py")
    assert "test_x.py" in str(exc.value)
    assert expected in str(exc.value)


# --- the live tree ---------------------------------------------------------


def test_every_block_in_the_real_tier_is_readable():
    """A malformed block goes red HERE, not only in the pre-push gate."""
    _, _, errors = mod.collect(REPO_ROOT / "tests" / "e2e")
    assert not errors, "\n".join(errors)


def test_render_lists_the_files_still_awaiting_a_row():
    (row,) = mod.parse_rows(WELL_FORMED, "test_x.py")
    out = mod.render([row], ["test_pending.py", "test_other.py"])
    assert "**1 of 3 files catalogued — 1 flow.**" in out
    assert "- `test_pending.py`" in out
    assert "2 files carry no flow block yet" in out


# --- one file, several flows ------------------------------------------------


TWO_FLOWS = WELL_FORMED.replace(
    'why:    otherwise the close deletes the operator\'s cwd\n"""',
    "why:    otherwise the close deletes the operator's cwd\n"
    "\n"
    "flow:   the same maintainer closes it from the main checkout\n"
    "cmds:\n"
    "    rack transition HATS-1 done\n"
    "expect: the close succeeds\n"
    'why:    the guard must not block the legitimate path\n"""',
)


def test_a_second_flow_opens_a_second_row_sharing_the_files_pins():
    first, second = mod.parse_rows(TWO_FLOWS, "test_x.py")
    assert first.pins == second.pins == ["HATS-788"]
    assert second.flow == "the same maintainer closes it from the main checkout"
    assert second.cmds == ["rack transition HATS-1 done"]


def test_a_multi_flow_file_is_counted_once_but_renders_every_flow():
    rows = mod.parse_rows(TWO_FLOWS, "test_x.py")
    out = mod.render(rows, [])
    assert "**1 of 1 files catalogued — 2 flows.**" in out
    assert out.count("## `test_x.py`") == 1
    assert out.count("- **flow** —") == 2


# --- main() pending refusal and ACK bypass (HATS-1563) ---------------------


def test_main_uncatalogued_file_refuses_in_check_mode(tmp_path: Path, capsys):
    (tmp_path / "test_a.py").write_text(WELL_FORMED)
    (tmp_path / "test_pending.py").write_text('"""no block here"""\n')
    rc = mod.main(["--check", "--dir", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "refusal — 1 uncatalogued file(s):" in err
    assert "- test_pending.py" in err
    assert "Remedy: write the four-field block" in err
    assert "run `python scripts/gen_e2e_catalog.py --write`" not in err


def test_main_uncatalogued_file_refuses_and_writes_in_write_mode(tmp_path: Path, capsys):
    (tmp_path / "test_a.py").write_text(WELL_FORMED)
    (tmp_path / "test_pending.py").write_text('"""no block here"""\n')
    rc = mod.main(["--write", "--dir", str(tmp_path)])
    assert rc == 1
    assert (tmp_path / "CATALOG.md").exists()
    err = capsys.readouterr().err
    assert "refusal — 1 uncatalogued file(s):" in err
    assert "- test_pending.py" in err


def test_main_uncatalogued_file_bypassed_with_env_ack(tmp_path: Path, monkeypatch, capsys):
    (tmp_path / "test_a.py").write_text(WELL_FORMED)
    (tmp_path / "test_pending.py").write_text('"""no block here"""\n')
    monkeypatch.setenv("AI_HATS_E2E_CATALOG_ACK", "1")

    # First write catalog with ACK
    rc_write = mod.main(["--write", "--dir", str(tmp_path)])
    assert rc_write == 0
    err_write = capsys.readouterr().err
    assert "BYPASSED via AI_HATS_E2E_CATALOG_ACK=1" in err_write

    # Then check catalog with ACK
    rc_check = mod.main(["--check", "--dir", str(tmp_path)])
    assert rc_check == 0
    err_check = capsys.readouterr().err
    assert "BYPASSED via AI_HATS_E2E_CATALOG_ACK=1" in err_check


def test_main_malformed_block_not_bypassed_by_env_ack(tmp_path: Path, monkeypatch, capsys):
    (tmp_path / "test_bad.py").write_text('"""e2e (HATS-1)\nflow: only flow field\n"""\n')
    monkeypatch.setenv("AI_HATS_E2E_CATALOG_ACK", "1")
    rc = mod.main(["--check", "--dir", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "malformed flow block(s):" in err
