"""The e2e harness's env scrub (HATS-685, HATS-876).

Subject: ``_helpers.env`` plus the two harness call sites that must not inherit
an ambient env — ``Project.run`` and ``build_launcher_venv``. An inherited
``PYTHONPATH`` redirects a launcher subprocess back at the source tree, so the
run stops testing the packaged artefact; ``GIT_*`` leaks the outer repo.
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


def test_merge_ack_is_asked_for_never_assumed(tmp_path):
    """HATS-1682 T4: the launcher env grants a merge only when asked.

    It used to ``setdefault`` the flag, so the whole e2e tier ran in the very
    environment the live probe named as the incident condition — no test there
    could observe a hole on the road into master. ``merge_ack`` is now the only
    source: OFF by default, and an inherited flag is dropped rather than
    obeyed, so a developer who exports one cannot keep the tier green.
    """
    assert "AI_HATS_MERGE_ACK" not in ENV_DENYLIST  # not a leak; a decision
    plain = launcher_subprocess_env({}, repo_url="/c", venv="/v", user_home=tmp_path)
    assert "AI_HATS_MERGE_ACK" not in plain
    inherited = launcher_subprocess_env(
        {"AI_HATS_MERGE_ACK": "1"}, repo_url="/c", venv="/v", user_home=tmp_path
    )
    assert "AI_HATS_MERGE_ACK" not in inherited
    asked = launcher_subprocess_env(
        {}, repo_url="/c", venv="/v", user_home=tmp_path, merge_ack=True
    )
    assert asked["AI_HATS_MERGE_ACK"] == "1"


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
    out = launcher_subprocess_env(base, repo_url="/clone", venv="/venv", user_home=user_home)

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


def test_project_run_scrubs_ambient_env(monkeypatch, tmp_path):
    """HATS-1129: Project.run must scrub ambient ENV_DENYLIST from os.environ."""
    from _helpers.project import Project

    monkeypatch.setenv(ENV_AI_HATS_DIR, "/leak/ambient/.agent")
    monkeypatch.setenv("PYTHONPATH", "/leak/src")

    captured_env = None

    # Permissive on purpose (HATS-1247): a double that pins one caller's exact argument
    # list explodes as soon as any other call site reaches this patched global.
    def fake_subprocess_run(cmd, *args, **kwargs):
        nonlocal captured_env
        captured_env = kwargs.get("env")
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)

    project = Project(path=tmp_path, ai_hats_binary=tmp_path / "bin" / "ai-hats")
    project.run("self", "update")

    assert captured_env is not None, "Project.run must pass an explicit env, not inherit"
    assert ENV_AI_HATS_DIR not in captured_env
    assert "PYTHONPATH" not in captured_env


def test_build_src_clone_is_bounded(monkeypatch, tmp_path):
    """HATS-1247: the per-worker clone must not run unbounded.

    Only the timeout is asserted here. Git plumbing isolation is NOT this call's job:
    the autouse ``_isolate_git_env`` fixture already strips ``GIT_*`` for every test
    (HATS-886), and passing an ``os.environ``-derived env would re-leak the very class
    ``test_git_env_hygiene`` guards against.
    """
    import _helpers.repo_src as repo_src

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    # Fresh memo: a faked clone must never leave a path nothing created in the real
    # module-level cache, which the rest of this worker installs from.
    monkeypatch.setattr(repo_src, "_CACHE", {})

    captured = {}

    def fake_subprocess_run(cmd, *args, **kwargs):
        captured.update(kwargs)
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)

    repo_src.build_src(tmp_path)

    assert captured.get("timeout"), "clone must be bounded so a hang cannot stall a worker"


def test_build_launcher_venv_scrubs_ambient_env(monkeypatch, tmp_path):
    """HATS-1129: build_launcher_venv must scrub ambient ENV_DENYLIST from os.environ.

    HATS-1247: ``build_src`` is stubbed, not exercised. Under xdist it takes its
    per-worker ``git clone`` branch and would reach the patched ``subprocess.run``,
    which is what made this test xdist-only red. Worse, a faked clone still populates
    ``repo_src._CACHE`` with a path nothing created, poisoning every later test on the
    worker that installs from ``build_src``. The subject here is the env scrub.
    """
    from _helpers.venv import build_launcher_venv

    monkeypatch.setenv(ENV_AI_HATS_DIR, "/leak/ambient/.agent")
    monkeypatch.setenv("PYTHONPATH", "/leak/src")
    monkeypatch.setattr("_helpers.repo_src.build_src", lambda repo_root: repo_root)

    captured_envs = []

    # Permissive on purpose (HATS-1247): a double that pins one caller's exact argument
    # list explodes as soon as any other call site reaches this patched global.
    def fake_subprocess_run(cmd, *args, **kwargs):
        captured_envs.append(kwargs.get("env"))
        if "install-launcher.sh" in cmd[1]:
            bin_path = tmp_path / "bin" / "ai-hats"
            bin_path.parent.mkdir(parents=True, exist_ok=True)
            bin_path.write_text("#!/bin/sh\n")
            bin_path.chmod(0o755)
        else:
            py = tmp_path / "bootstrap" / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("#!/bin/sh\n")
            py.chmod(0o755)
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0, stdout="", stderr="")

    script = tmp_path / "scripts" / "install-launcher.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\n")

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)

    build_launcher_venv(tmp_path, tmp_path)

    assert captured_envs
    for env in captured_envs:
        # Inheriting the ambient env IS the leak under guard, so an absent env is a
        # failure in its own right — not a call to skip over.
        assert env is not None, "build_launcher_venv must pass an explicit env, not inherit"
        assert ENV_AI_HATS_DIR not in env
        assert "PYTHONPATH" not in env
