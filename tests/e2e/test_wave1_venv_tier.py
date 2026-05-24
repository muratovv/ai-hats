"""Wave 1 venv-tier pilots — prove ``tmp_venv_project`` module fixture.

These tests exercise the launcher-tier path: a real ``ai-hats``
launcher binary, its inner venv, and real ``ai-hats self <cmd>``
invocations against a fresh project dir. Marked ``integration`` —
opt out with ``pytest -m "not integration"`` while iterating.

Maps to Core scenarios:

* ``test_self_init_is_idempotent_on_repeat`` → S-CLI-36. Two
  back-to-back ``self init`` calls; the second must succeed without
  re-prompting and without trashing the existing yaml.
* ``test_venv_project_yaml_exists`` → trivial reuse check. Lives
  alongside the idempotency test so we can EYEBALL module-scoped
  reuse — if the venv were rebuilt for this second test, total
  wall-clock would roughly double.

Skips the whole module when the launcher install or ``self update``
can't run (no ``install-launcher.sh``, no network + no warm pip
cache). Build budget: <120s for first test, <5s for the reuse check.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


def test_self_init_is_idempotent_on_repeat(tmp_venv_project) -> None:
    """``self init`` twice with the same flags exits 0 both times."""
    args = ("self", "init", "-r", "assistant", "-p", "claude", "--no-update")
    tmp_venv_project.run(*args, timeout=120).expect_ok().expect_file("ai-hats.yaml")
    tmp_venv_project.run(*args, timeout=120).expect_ok().expect_file(
        "ai-hats.yaml", contains="default_role: assistant",
    )


def test_venv_project_yaml_exists(tmp_venv_project) -> None:
    """Module-scoped reuse proof — second test sees the same project
    that the idempotency test populated, no fixture rebuild required.
    Wall-clock for this test alone should be sub-second."""
    assert tmp_venv_project.yaml.is_file()
    assert "default_role: assistant" in tmp_venv_project.yaml.read_text()
