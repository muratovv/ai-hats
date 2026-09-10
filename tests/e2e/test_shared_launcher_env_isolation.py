"""e2e (HATS-828, HATS-876)

flow:   a maintainer running e2e test suite gate using shared launcher fixture
cmds:
    bash scripts/run-e2e-gate.sh
expect: shared launcher fixture isolates environment variables preventing state leak across
        test runs
why:    without launcher environment isolation, e2e tests pollute environment variables for
        sibling test runs
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _helpers.env import launcher_subprocess_env
from ai_hats.constants import ENV_REPO_URL

pytestmark = pytest.mark.gates


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
        [str(launcher), "self", "init", "-r", "assistant", "-p", "claude", "--task-prefix", "TST"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
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
