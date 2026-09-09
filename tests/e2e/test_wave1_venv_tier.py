"""e2e (HATS-478)

flow:   a developer running self init repeatedly using launcher venv fixtures
cmds:
    ai-hats self init -r assistant -p claude --no-update
expect: repeated init commands execute idempotently and reuse shared launcher venvs
        across tests
why: without venv fixture reuse across tests, e2e test suites spend excessive time
     building duplicate
        virtual environments"""

from __future__ import annotations

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG


pytestmark = [pytest.mark.integration, pytest.mark.install]


def test_self_init_is_idempotent_on_repeat(tmp_venv_project) -> None:
    """``self init`` twice with the same flags exits 0 both times."""
    args = ("self", "init", "-r", "assistant", "-p", "claude", "--no-update")
    tmp_venv_project.run(*args, timeout=120).expect_ok().expect_file(PROJECT_CONFIG)
    tmp_venv_project.run(*args, timeout=120).expect_ok().expect_file(
        PROJECT_CONFIG,
        contains="default_role: assistant",
    )


def test_shared_venv_reused_across_tests(tmp_venv_project) -> None:
    """Reuse proof — the function-scoped Project sees a fresh empty
    project dir AND points at the already-built shared venv via
    ``AI_HATS_VENV``. The venv directory exists, its python is on
    disk, and the project's own ``.agent/`` hasn't been populated
    yet (clean slate)."""
    from pathlib import Path

    shared_venv = Path(tmp_venv_project.env[ENV_AI_HATS_VENV])
    assert (shared_venv / "bin" / "python").is_file(), (
        "shared venv not visible to second test — reuse broken"
    )
    assert not tmp_venv_project.yaml.exists(), "fresh project should not carry yaml across tests"
    assert not (tmp_venv_project.path / ".agent").exists(), (
        "fresh project should not carry .agent/ across tests"
    )
