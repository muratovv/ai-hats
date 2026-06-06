"""Unit guard for the e2e subprocess env-scrub (HATS-685).

Pure-function test (no pip, no subprocess) — fast, runs in the normal suite.
Guards the denylist that keeps redirect vars (chiefly ``PYTHONPATH``) from
leaking into real-install e2e subprocesses. Fail-under-revert: with
``clean_env`` / ``ENV_DENYLIST`` removed, the import + assertions fail.

Why it matters: ``src/ai_hats/`` has no ``library/`` subdir — it maps to the
``ai_hats.library`` package only at build time. An inherited
``PYTHONPATH=<repo>/src`` (the worktree workaround, and what ``ai-hats wt exec``
sets) redirects a launcher subprocess's ``ai_hats`` import to the source tree,
where ``files("ai_hats.library")`` raises ``ModuleNotFoundError`` → built-in
roles vanish. The scrub removes that class of leak.
"""

from __future__ import annotations

from _helpers.env import ENV_DENYLIST, clean_env


def test_clean_env_strips_denylist_keeps_rest():
    base = {
        "PYTHONPATH": "/leak/src",
        "VIRTUAL_ENV": "/leak/venv",
        "PYTHONHOME": "/leak/home",
        "PYTHONSTARTUP": "/leak/startup.py",
        "AI_HATS_DIR": "/leak/.agent",
        "AI_HATS_USER_HOME": "/leak/home",
        "PATH": "/usr/bin",
        "HOME": "/home/me",
        "AI_HATS_REPO_URL": "/repo",
    }
    out = clean_env(base)

    # Every denylist member is stripped.
    for key in ENV_DENYLIST:
        assert key not in out, f"{key} leaked through clean_env"
    # Non-denylist vars are preserved verbatim.
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/home/me"
    assert out["AI_HATS_REPO_URL"] == "/repo"
    # The input dict is not mutated.
    assert "PYTHONPATH" in base


def test_clean_env_denylist_covers_pythonpath():
    """PYTHONPATH is the proven culprit — it must be in the denylist."""
    assert "PYTHONPATH" in ENV_DENYLIST
