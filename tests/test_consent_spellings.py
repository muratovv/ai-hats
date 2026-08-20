"""HATS-1754 — the guard must see a guarded call whatever runner delivers it.

Measured 2026-08-20: `uv run ai-hats wt merge` and
`python3 -m ai_hats_rack transition HATS-1 execute` returned NO decision at all,
while `timeout 60 ai-hats wt merge` raised the question. `WRAPPERS` knew the
process wrappers and no language runner, so the head binary read as `uv` or
`python3` and the guarded call behind it was never looked for.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = (
    REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard" / "hooks"
)
SPELLINGS = HOOKS / "consent_spellings.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def spellings():
    return _load(SPELLINGS, "consent_spellings")


def test_the_table_is_shipped_beside_the_hooks_that_read_it():
    """Green must mean 'checked and clean', never 'the file moved'."""
    assert SPELLINGS.is_file(), f"the gate imports a sibling that is not there: {SPELLINGS}"


@pytest.mark.parametrize(
    "token,expected",
    [
        ("python", True),
        ("python3", True),
        ("python3.11", True),
        (".venv/bin/python", True),
        ("/usr/bin/python3.12", True),
        ("uv", False),
        ("node", False),
        ("pythonic-tool", False),
    ],
)
def test_an_interpreter_is_recognised_behind_any_path(spellings, token, expected):
    assert spellings.is_interpreter(token) is expected


@pytest.mark.parametrize(
    "tokens,expected",
    [
        (
            ["python3", "-m", "ai_hats_rack", "transition", "HATS-1", "execute"],
            ["rack", "transition", "HATS-1", "execute"],
        ),
        (
            [".venv/bin/python", "-m", "ai_hats", "wt", "merge", "task/x"],
            ["ai-hats", "wt", "merge", "task/x"],
        ),
        (["python", "-m", "ai_hats_rack.cli", "ls"], ["rack", "ls"]),
    ],
)
def test_a_module_call_reads_as_the_binary_it_runs(spellings, tokens, expected):
    """The checks downstream read ``args[1:]`` — so the binary must come first."""
    assert spellings.module_binary(tokens) == expected


@pytest.mark.parametrize(
    "tokens",
    [
        ["rack", "transition", "HATS-1", "execute"],  # no interpreter
        ["python3", "-c", "print('hi')"],  # not a module call
        ["python3", "-m"],  # nothing named
        ["python3", "-m", "pytest", "tests/"],  # a module we guard nothing about
        [],
    ],
)
def test_what_is_not_a_guarded_module_call_resolves_to_nothing(spellings, tokens):
    """A table that answers for everything would make every slice a candidate."""
    assert spellings.module_binary(tokens) == []


def test_every_mapped_module_can_actually_be_run(spellings):
    """`python -m ai_hats.cli` exits 1 — a probe on it reports an unwalkable hole.

    The rule is mechanical: a package needs a `__main__`, a module is its own.
    """
    for module in spellings.MODULE_BINARIES:
        spec = importlib.util.find_spec(module)
        assert spec is not None, f"{module!r} does not resolve — the table names a ghost"
        if spec.submodule_search_locations is None:
            continue  # a plain module: `-m` runs it as __main__
        assert importlib.util.find_spec(f"{module}.__main__") is not None, (
            f"{module!r} is a package with no __main__ — `python -m {module}` exits 1"
        )


@pytest.mark.integration
def test_a_missing_table_costs_the_guard_its_sight_and_says_so(tmp_path):
    """The fail-open branch, exercised — an unrecorded one is indistinguishable
    from a gate that simply had nothing to say (`dev_rule_silent_fallback`)."""
    hooks = tmp_path / "hooks"
    shutil.copytree(
        HOOKS, hooks, ignore=shutil.ignore_patterns("consent_spellings.py", "__pycache__")
    )
    assert not (hooks / "consent_spellings.py").exists()

    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "uv run rack transition HATS-1 execute"}}
    )
    result = subprocess.run(  # noqa: S603 - interpreter and script path are ours
        [sys.executable, str(hooks / "safety_gate.py")],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=60,
    )

    assert result.returncode == 0, f"the guard died instead of failing open: {result.stderr}"
    assert "consent_spellings.py" in result.stderr, (
        f"the guard lost its sight of every runner spelling in silence: {result.stderr!r}"
    )
