"""E2E regression: a leaked absolute PYTHONPATH must not hide built-in roles
in a raw ``shared_launcher`` consumer (HATS-828).

Bug: the session-scoped ``shared_launcher`` fixture captured ``os.environ`` at
SESSION setup — before the function-scoped autouse scrubs apply — so a suite
launched with ``PYTHONPATH=<repo>/src`` exported (the worktree-dev workaround /
what ``ai-hats wt exec`` sets) leaked that absolute path into every raw
consumer's subprocess. A non-editable ai-hats install nests ``ai_hats.library``
under ``site-packages/ai_hats/``; the leaked ``src`` shadows ``ai_hats`` →
``import ai_hats.library`` fails → ``_builtin_library_layers()`` is empty →
``ai-hats self init -r assistant`` exits 1 with "Role 'assistant' not found".
CI never exports PYTHONPATH, so it stayed green and the bug was invisible there.

This test reconstructs the leak deterministically (independent of how the suite
itself is launched): it injects an **absolute** ``PYTHONPATH=<repo>/src`` into a
copy of the fixture's base env, then rebuilds the subprocess env through the SAME
``launcher_subprocess_env`` transform the fixture uses, and asserts the built-in
``assistant`` role still resolves.

Fail-under-revert: make ``launcher_subprocess_env`` a pass-through (or revert
``shared_launcher`` to ``os.environ.copy()``) → the absolute PYTHONPATH survives
→ ``self init -r assistant`` exits 1, "Role 'assistant' not found. Available
roles: <dev's user-library roles>".

A *relative* ``PYTHONPATH=src`` would NOT reproduce — it resolves against the
launcher subprocess's cwd (the tmp project), not the repo — so the absolute path
is load-bearing here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _helpers.env import launcher_subprocess_env
from ai_hats.constants import ENV_REPO_URL


@pytest.mark.integration
def test_absolute_pythonpath_leak_does_not_hide_builtin_roles(
    shared_launcher, repo_root: Path, tmp_path, tmp_path_factory
):
    launcher, base_env, shared_venv = shared_launcher

    # Inject the wt-exec leak (ABSOLUTE — relative would not shadow), then
    # rebuild the env through the fixture's own transform.
    leaked_base = {**base_env, "PYTHONPATH": str(repo_root / "src")}
    env = launcher_subprocess_env(
        leaked_base,
        repo_url=base_env[ENV_REPO_URL],
        venv=shared_venv,
        user_home=tmp_path_factory.mktemp("isolation-user-home"),
    )

    # Pure behavioral assertion: drive the REAL binary and prove the built-in
    # role composes. (The transform-level "PYTHONPATH dropped" guarantee is owned
    # by the unit test ``test_launcher_subprocess_env_isolates_and_pins`` — this
    # test must reach ``self init`` so a reverted transform reproduces the actual
    # "Role 'assistant' not found" bug rather than short-circuiting.)
    project = tmp_path / "project"
    project.mkdir()
    res = subprocess.run(
        [str(launcher), "self", "init", "-r", "assistant", "-p", "claude",
         "--task-prefix", "TST"],
        cwd=str(project), env=env, capture_output=True, text=True, timeout=180,
    )
    assert res.returncode == 0, (
        "🐛 HATS-828 REGRESSION: built-in 'assistant' role vanished under a "
        f"leaked absolute PYTHONPATH (exit {res.returncode})\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    # The built-in role actually composed (not just a 0 exit on some other path).
    assert "assistant" in res.stdout.lower(), (
        f"self init did not report the assistant role:\n{res.stdout}"
    )
