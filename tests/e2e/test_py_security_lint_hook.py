"""HATS-660 — script-level behaviour of the py-security-lint PostToolUse hook.

Per ``dev_rule_e2e_gate`` the hook is a pure subprocess surface. We feed it Claude
Code ``PostToolUse`` payloads on stdin and assert the NON-BLOCKING contract: on a
``.py`` file with a ruff ``S`` (flake8-bandit) finding it emits
``hookSpecificOutput.additionalContext`` forwarding the finding; otherwise it is
silent. It NEVER emits a ``permissionDecision`` and NEVER blocks. Fail-open on any
error (ruff absent, non-.py, garbage payload).
"""
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
    / "library/usage/skills/py-security-lint/hooks/py_security_lint.py"
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
