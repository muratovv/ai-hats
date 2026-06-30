"""HATS-842 — script-level behaviour of the comment-length-lint PostToolUse hook.

Per ``dev_rule_e2e_gate`` the hook is a pure subprocess surface. We feed it Claude
Code ``PostToolUse`` payloads on stdin and assert the NON-BLOCKING contract: on a
``.py`` file carrying an oversized comment block or docstring it emits
``hookSpecificOutput.additionalContext``; otherwise it is silent. It NEVER emits a
``permissionDecision`` and NEVER blocks. Fail-open on any error (non-.py, garbage
payload, syntax error). The RED baseline is the HATS-837 shape: a 4-line DI-wiring
comment + a multi-paragraph docstring; the healthy ≈9-line contract docstring that
review kept stays silent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = (
    REPO_ROOT
    / "library/usage/skills/comment-length-lint/hooks/comment_length_lint.py"
)

# HATS-837 RED sample 1 — the 4-line DI-wiring comment that review trimmed to one.
DI_COMMENT = (
    "x = 1\n"
    "# HATS-837: the managed-hook concern lives in a dedicated DI dependency.\n"
    "# Narrow seam — it gets the project dir, a live config reference, and a\n"
    "# compose callable (the one back-coupling), so it never imports Assembler\n"
    "# at load time.\n"
    "y = 2\n"
)

# HATS-837 RED sample 2 — a multi-paragraph docstring essay (> 10 lines).
BLOATED_DOCSTRING = (
    "def g():\n"
    '    """Owns the cohesive hook cluster the Assembler used to carry inline.\n'
    "\n"
    "    Paragraph two restating the wiring in prose.\n"
    "    Line four of the essay.\n"
    "    Line five of the essay.\n"
    "    Line six of the essay.\n"
    "    Line seven of the essay.\n"
    "    Line eight of the essay.\n"
    "    Line nine of the essay.\n"
    "    Line ten of the essay.\n"
    "    Line eleven that pushes it past the line threshold.\n"
    '    """\n'
    "    return None\n"
)

# The healthy contract docstring review KEPT (≈9 lines / ≈470 chars) — must stay silent.
HEALTHY_DOCSTRING = (
    "def materialize_runtime_hooks(result=None):\n"
    '    """Materialize runtime-hook scripts to the managed library hooks dir.\n'
    "\n"
    "    Two sources under one manifest: the package-data guards (the shared-state\n"
    "    safety net, must exist on disk) and each composed skill's declared\n"
    "    runtime_hooks script. result is None on the bare-bump path, leaving only\n"
    "    the guards. Idempotent; raises HookError on a broken install.\n"
    '    """\n'
    "    return None\n"
)

TERSE_OK = (
    "lock = FileLock(path)  # flock auto-releases on PID death — no stale cleanup\n"
    "def f():\n"
    '    """Write the session\'s runtime hooks; return the paths written."""\n'
    "    return None\n"
)

# 4 *trailing* (non-standalone) comments — not a comment block; must stay silent.
INLINE_TRAILERS = "a = 1  # one\nb = 2  # two\nc = 3  # three\nd = 4  # four\n"


def _run(file_path, *, env_extra=None, raw=None):
    if raw is not None:
        payload = raw
    else:
        payload = json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(file_path)},
            }
        )
    env = os.environ.copy()
    for k in (
        "AI_HATS_COMMENT_LINT_OFF",
        "AI_HATS_COMMENT_MAX_LINES",
        "AI_HATS_DOCSTRING_MAX_LINES",
        "AI_HATS_DOCSTRING_MAX_CHARS",
    ):
        env.pop(k, None)
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


def _write(tmp_path, src, name="m.py"):
    f = tmp_path / name
    f.write_text(src)
    return f


@pytest.mark.integration
def test_di_comment_block_flagged(tmp_path):
    res = _run(_write(tmp_path, DI_COMMENT))
    assert res.returncode == 0, res.stderr
    ctx = _ctx(res)
    assert ctx is not None and "comment block of 4 lines" in ctx
    assert "permissionDecision" not in res.stdout


@pytest.mark.integration
def test_bloated_docstring_flagged(tmp_path):
    res = _run(_write(tmp_path, BLOATED_DOCSTRING))
    assert res.returncode == 0, res.stderr
    ctx = _ctx(res)
    assert ctx is not None and "docstring on 'g'" in ctx


@pytest.mark.integration
def test_healthy_contract_docstring_silent(tmp_path):
    res = _run(_write(tmp_path, HEALTHY_DOCSTRING))
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None, f"healthy docstring should not flag: {res.stdout!r}"


@pytest.mark.integration
def test_terse_comment_and_docstring_silent(tmp_path):
    res = _run(_write(tmp_path, TERSE_OK))
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None


@pytest.mark.integration
def test_inline_trailing_comments_not_a_block(tmp_path):
    res = _run(_write(tmp_path, INLINE_TRAILERS))
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None


@pytest.mark.integration
def test_threshold_env_override_silences_comment(tmp_path):
    res = _run(
        _write(tmp_path, DI_COMMENT),
        env_extra={"AI_HATS_COMMENT_MAX_LINES": "10"},
    )
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None


@pytest.mark.integration
def test_kill_switch_disables_hook(tmp_path):
    res = _run(_write(tmp_path, DI_COMMENT), env_extra={"AI_HATS_COMMENT_LINT_OFF": "1"})
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None


@pytest.mark.integration
def test_non_py_file_is_silent(tmp_path):
    res = _run(_write(tmp_path, DI_COMMENT, name="notes.txt"))
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None


@pytest.mark.integration
def test_syntax_error_fails_open(tmp_path):
    res = _run(_write(tmp_path, "def (:\n  # a\n  # b\n  # c\n  # d\n"))
    assert res.returncode == 0, res.stderr
    # comment-run detection still works on a tokenizable prefix; the contract is
    # only that it never crashes / blocks.
    assert "permissionDecision" not in res.stdout


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
