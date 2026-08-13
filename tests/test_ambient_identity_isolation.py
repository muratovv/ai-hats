"""The suite must never inherit the developer session's identity (HATS-1622).

An inherited ``AI_HATS_SESSION_IDENTITY`` gives a throwaway tmp project the
developer's role, whose check bindings a linked worktree then resolves into its
own library copy — D9 clause 4 refuses, and 26 sound tests turn red.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_IDENTITY = json.dumps(
    {
        "id": "20260101-000000-1-00000",
        "project_dir": "/nonexistent/project",
        "provider": "claude",
        "role": "maintainer",
        "session_dir": "/nonexistent/project/.agent/ai-hats/sessions/runs/session_x",
        "skills_root": "/nonexistent/mirror/skills",
        "v": 1,
    }
)

_INNER = """
import os


def test_no_ambient_identity():
    assert os.environ.get("AI_HATS_SESSION_IDENTITY") is None
"""


def _root_conftest() -> str:
    return (REPO_ROOT / "conftest.py").read_text(encoding="utf-8")


def test_root_conftest_drops_an_inherited_identity(pytester, monkeypatch):
    monkeypatch.setenv("AI_HATS_SESSION_IDENTITY", _IDENTITY)
    pytester.makeconftest(_root_conftest())
    pytester.makepyfile(test_inner=_INNER)
    pytester.runpytest_subprocess("test_inner.py", "-q").assert_outcomes(passed=1)


def test_the_drop_is_what_makes_the_guard_pass(pytester, monkeypatch):
    """Strip the drop and the child inherits it — without this, the assertion
    above could pass on a suite that never received the variable at all."""
    monkeypatch.setenv("AI_HATS_SESSION_IDENTITY", _IDENTITY)
    # Both spellings: the drop moved from a literal pop to `drop_identity`, and a
    # mutation that no longer removes it would silently stop testing anything.
    stripped = "\n".join(
        line
        for line in _root_conftest().splitlines()
        if "AI_HATS_SESSION_IDENTITY" not in line and "drop_identity" not in line
    )
    pytester.makeconftest(stripped)
    pytester.makepyfile(test_inner=_INNER)
    pytester.runpytest_subprocess("test_inner.py", "-q").assert_outcomes(failed=1)
