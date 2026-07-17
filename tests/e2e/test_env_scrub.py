"""Unit guard for the e2e subprocess env-scrub (HATS-685).

Pure-function test (no pip, no subprocess) — fast, runs in the normal suite.
Guards the denylist that keeps redirect vars (chiefly ``PYTHONPATH``) from
leaking into real-install e2e subprocesses. Fail-under-revert: with
``clean_env`` / ``ENV_DENYLIST`` removed, the import + assertions fail.

Why it matters: an inherited ``PYTHONPATH`` (the worktree workaround, and what
``ai-hats wt exec`` sets) redirects a launcher subprocess's imports to the source
tree instead of the real install. Post-HATS-876 the library is the separate
``ai_hats_library`` package (also on that ``PYTHONPATH``), so a leak runs
source-against-source; pre-HATS-876 it failed loud (``files("ai_hats.library")``
→ ``ModuleNotFoundError`` → built-in roles vanish). The scrub removes that class
of leak.
"""

from __future__ import annotations

from _helpers.env import ENV_DENYLIST, clean_env, launcher_subprocess_env
from ai_hats.paths import ENV_AI_HATS_DIR, ENV_AI_HATS_VENV
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL


def test_clean_env_strips_denylist_keeps_rest():
    base = {
        "PYTHONPATH": "/leak/src",
        "VIRTUAL_ENV": "/leak/venv",
        "PYTHONHOME": "/leak/home",
        "PYTHONSTARTUP": "/leak/startup.py",
        ENV_AI_HATS_DIR: "/leak/.agent",
        "AI_HATS_USER_HOME": "/leak/home",
        "GIT_DIR": "/leak/.git",
        "GIT_WORK_TREE": "/leak",
        "GIT_INDEX_FILE": "/leak/.git/index",
        "PATH": "/usr/bin",
        "HOME": "/home/me",
        ENV_REPO_URL: "/repo",
    }
    out = clean_env(base)

    # Every denylist member is stripped.
    for key in ENV_DENYLIST:
        assert key not in out, f"{key} leaked through clean_env"
    # Non-denylist vars are preserved verbatim.
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/home/me"
    assert out[ENV_REPO_URL] == "/repo"
    # The input dict is not mutated.
    assert "PYTHONPATH" in base


def test_clean_env_denylist_covers_pythonpath():
    """PYTHONPATH is the proven culprit — it must be in the denylist."""
    assert "PYTHONPATH" in ENV_DENYLIST


def test_clean_env_denylist_covers_git_plumbing():
    """HATS-887: the session-scoped shared_launcher captures env before the
    function-scoped GIT_* strip, so the denylist itself must drop the plumbing
    vars. RED-under-revert: drop them from ENV_DENYLIST and this fails."""
    assert {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"} <= ENV_DENYLIST


def test_merge_ack_inherits_and_defaults(tmp_path):
    """HATS-1019: yolo-mode rides plain env inheritance — the consent flag
    must never join the scrub list, must survive the launcher transform,
    and the hermetic e2e env grants it by default (the merge inventory
    tests merge semantics, not consent)."""
    assert "AI_HATS_MERGE_ACK" not in ENV_DENYLIST
    out = launcher_subprocess_env(
        {"AI_HATS_MERGE_ACK": "1"}, repo_url="/c", venv="/v", user_home=tmp_path
    )
    assert out["AI_HATS_MERGE_ACK"] == "1"
    granted = launcher_subprocess_env(
        {}, repo_url="/c", venv="/v", user_home=tmp_path
    )
    assert granted["AI_HATS_MERGE_ACK"] == "1"


def test_launcher_subprocess_env_isolates_and_pins(tmp_path):
    """HATS-828: the ``shared_launcher`` env transform drops the leak + pins home.

    Fail-under-revert: make ``launcher_subprocess_env`` a pass-through (or revert
    the fixture to ``os.environ.copy()``) → ``PYTHONPATH`` survives and
    ``AI_HATS_USER_HOME`` falls back to the dev's real home → the e2e regression
    (``test_shared_launcher_env_isolation``) reports "Role 'assistant' not found".
    """
    user_home = tmp_path / "empty_home"
    base = {
        # The absolute-PYTHONPATH leak that hides the built-in library.
        "PYTHONPATH": "/repo/src",
        # An inherited user-home that must NOT win over the explicit pin.
        "AI_HATS_USER_HOME": "/dev/.config",
        # A stray launcher-dest that could redirect a child install.
        ENV_LAUNCHER_DEST: "/leak/bin/ai-hats",
        # Innocuous vars that MUST be preserved (HOME → warm uv cache + auth).
        "PATH": "/usr/bin",
        "HOME": "/home/me",
    }
    out = launcher_subprocess_env(
        base, repo_url="/clone", venv="/venv", user_home=user_home
    )

    # The leak is gone.
    assert "PYTHONPATH" not in out
    assert ENV_LAUNCHER_DEST not in out
    # AI_HATS_USER_HOME is re-pinned to the explicit empty dir, not the inherited.
    assert out["AI_HATS_USER_HOME"] == str(user_home)
    # Install-source knobs are set.
    assert out[ENV_REPO_URL] == "/clone"
    assert out[ENV_AI_HATS_VENV] == "/venv"
    # HOME (and other innocuous vars) ride through untouched.
    assert out["HOME"] == "/home/me"
    assert out["PATH"] == "/usr/bin"
    # Pure: the input dict is not mutated.
    assert base["PYTHONPATH"] == "/repo/src"
    assert base["AI_HATS_USER_HOME"] == "/dev/.config"
