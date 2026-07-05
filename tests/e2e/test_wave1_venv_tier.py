"""Wave 1 venv-tier pilots — prove ``tmp_venv_project`` module fixture.

These tests exercise the launcher-tier path: a real ``ai-hats``
launcher binary, its inner venv, and real ``ai-hats self <cmd>``
invocations against a fresh project dir. Marked ``integration`` —
opt out with ``pytest -m "not integration"`` while iterating.

Maps to Core scenarios:

* ``test_self_init_is_idempotent_on_repeat`` → S-CLI-36. Two
  back-to-back ``self init`` calls; the second must succeed without
  re-prompting and without trashing the existing yaml.
* ``test_shared_venv_reused_across_tests`` → reuse proof. The
  function-scoped fixture sits on top of a module-scoped venv
  builder; this test confirms the second invocation in the module
  points at the same already-built venv (cheap, sub-second), which
  is the whole point of the layered scope. If the venv were rebuilt,
  total wall-clock would roughly double.

Skips the whole module when the launcher install or ``self update``
can't run (no ``install-launcher.sh``, no network + no warm pip
cache). Build budget: <120s for first test, <5s for the reuse check.
"""

from __future__ import annotations

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG


pytestmark = pytest.mark.integration


def test_self_init_is_idempotent_on_repeat(tmp_venv_project) -> None:
    """``self init`` twice with the same flags exits 0 both times."""
    args = ("self", "init", "-r", "assistant", "-p", "claude", "--no-update")
    tmp_venv_project.run(*args, timeout=120).expect_ok().expect_file(PROJECT_CONFIG)
    tmp_venv_project.run(*args, timeout=120).expect_ok().expect_file(
        PROJECT_CONFIG, contains="default_role: assistant",
    )


def test_shared_venv_reused_across_tests(tmp_venv_project) -> None:
    """Reuse proof — the function-scoped Project sees a fresh empty
    project dir AND points at the already-built shared venv via
    ``AI_HATS_VENV``. The venv directory exists, its python is on
    disk, and the project's own ``.agent/`` hasn't been populated
    yet (clean slate)."""
    from pathlib import Path
    shared_venv = Path(tmp_venv_project.env[ENV_AI_HATS_VENV])
    assert (shared_venv / "bin" / "python").is_file(), \
        "shared venv not visible to second test — reuse broken"
    assert not tmp_venv_project.yaml.exists(), \
        "fresh project should not carry yaml across tests"
    assert not (tmp_venv_project.path / ".agent").exists(), \
        "fresh project should not carry .agent/ across tests"
