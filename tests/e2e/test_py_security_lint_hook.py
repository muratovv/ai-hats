"""e2e (HATS-660)

flow:   an agent editing python files with security vulnerabilities
cmds:
    # when editing python code containing security flaws
    git commit -m "update code"
expect: python security lint hook runs ruff security checks and outputs non-blocking
        warnings
why: without security lint hooks, vulnerable python coding patterns land in codebase
     without warning"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ai_hats.constants import HOOK_POST_TOOL_USE

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/usage/skills/py-security-lint/hooks/py_security_lint.py"
)
# ruff lives in the same venv bin as the interpreter running the tests.
VENV_BIN = Path(sys.executable).parent


def _run(file_path, *, env_extra=None, raw=None, with_ruff=True):
    if raw is not None:
        payload = raw
    else:
        payload = json.dumps(
            {
                "hook_event_name": HOOK_POST_TOOL_USE,
                "tool_name": "Edit",
                "tool_input": {"file_path": str(file_path)},
            }
        )
    env = os.environ.copy()
    env.pop("AI_HATS_SECURITY_LINT_OFF", None)
    # Make ruff discoverable (or not) deterministically.
    env["PATH"] = str(VENV_BIN) if with_ruff else "/nonexistent-path-h660"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )


def _ctx(res):
    out = res.stdout.strip()
    if not out:
        return None
    return json.loads(out).get("hookSpecificOutput", {}).get("additionalContext")


@pytest.mark.integration
def test_py_violation_forwards_ruff_finding(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def f(x):\n    return eval(x)\n")
    res = _run(f)
    assert res.returncode == 0, res.stderr
    ctx = _ctx(res)
    assert ctx is not None, f"expected a finding nudge, stdout={res.stdout!r}"
    assert "S307" in ctx  # ruff flake8-bandit: use of eval
    # Non-blocking contract: never a permissionDecision.
    assert "permissionDecision" not in res.stdout


@pytest.mark.integration
def test_a_rule_the_project_excluded_is_not_reported(tmp_path):
    """HATS-1591: the nudge names what the gate would refuse — no more.

    Forcing `--select S` reported rules this project excludes on purpose, so a
    repo that spawns processes by profession got twenty `S603`/`S607` lines on
    every edit and learned to scroll past the message entirely.
    """
    f = tmp_path / "spawns.py"
    f.write_text('import subprocess\n\n\ndef f():\n    subprocess.run(["git", "status"])\n')
    res = _run(f)
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None, f"an ignored rule was reported anyway: {res.stdout!r}"


@pytest.mark.integration
def test_clean_py_is_silent(tmp_path):
    f = tmp_path / "clean.py"
    f.write_text("def f(x):\n    return x + 1\n")
    res = _run(f)
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None, f"unexpected nudge: {res.stdout!r}"


@pytest.mark.integration
def test_non_py_file_is_silent(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("eval(x)\n")  # dangerous-looking, but not a .py
    res = _run(f)
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None


@pytest.mark.integration
def test_ruff_absent_fails_open(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def f(x):\n    return eval(x)\n")
    res = _run(f, with_ruff=False)  # ruff not on PATH
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None


@pytest.mark.integration
def test_kill_switch_disables_hook(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def f(x):\n    return eval(x)\n")
    res = _run(f, env_extra={"AI_HATS_SECURITY_LINT_OFF": "1"})
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None


@pytest.mark.integration
def test_garbage_payload_fails_open():
    res = _run(None, raw="not json {{{")
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None


@pytest.mark.integration
def test_missing_file_is_silent(tmp_path):
    res = _run(tmp_path / "does_not_exist.py")
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None
