"""HATS-1407 — the two bypass-journal writers must not drift apart.

The shell twin serves git hooks, the Python twin serves the stdlib-only
PreToolUse hooks. They are separate implementations of one line format, so a
field added to one and forgotten in the other would split the journal into two
incompatible halves that a reader silently mis-parses.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/hooks"
SH = HOOKS / "bypass_journal.sh"
PY = HOOKS / "bypass_journal.py"


def _python_fields() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("bypass_journal", PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bypass_journal"] = module
    spec.loader.exec_module(module)
    return module.FIELDS


def _shell_fields() -> tuple[str, ...]:
    """Read the exported schema by sourcing the file, as a consumer would."""
    res = subprocess.run(
        ["bash", "-c", f'. "{SH}" && printf "%s" "$AI_HATS_BYPASS_FIELDS"'],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return tuple(res.stdout.split())


def test_the_two_writers_declare_the_same_fields():
    assert _shell_fields() == _python_fields()


def test_the_shell_printf_emits_exactly_its_declared_fields():
    """The exported list is only a claim — check it against what is written."""
    emitted = tuple(re.findall(r'"([a-z_]+)":"%s"', SH.read_text()))
    assert emitted == _shell_fields()


@pytest.mark.integration
def test_both_writers_produce_the_same_keys_on_a_real_repo(tmp_path: Path):
    """End-to-end: one repo, one line from each writer, identical key sets."""
    subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)

    subprocess.run(
        ["bash", "-c", f'. "{SH}" && ai_hats_journal_bypass hatch SHELL_VAR'],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(HOOKS)!r});"
            " from bypass_journal import journal_bypass;"
            " journal_bypass('hatch', 'PY_VAR')",
        ],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )

    lines = (tmp_path / ".git/ai-hats/bypasses.jsonl").read_text().splitlines()
    assert len(lines) == 2, lines
    shell_entry, py_entry = (json.loads(line) for line in lines)

    assert set(shell_entry) == set(py_entry)
    assert shell_entry["reason"] == "SHELL_VAR"
    assert py_entry["reason"] == "PY_VAR"
