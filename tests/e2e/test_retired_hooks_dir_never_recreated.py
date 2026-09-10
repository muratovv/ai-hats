"""e2e (HATS-1480, HATS-1500)

flow:   a developer running self init or switching roles on an updated project
cmds:
    ai-hats self init -r hypothesis-intake -p claude --no-update
expect: deprecated library/hooks/ directory is never re-created during project
        initialization or role
        switches
why: without enforcing hook path retirement, role switches recreate deprecated flat hook
     directories"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.install, pytest.mark.library]


def test_retired_hooks_dir_never_recreated(tmp_venv_project) -> None:
    """HATS-1480 AC-1: neither a fresh init nor a role switch re-creates the copy."""
    project = tmp_venv_project.path

    tmp_venv_project.run(
        "self", "init", "-r", "maintainer", "-p", "claude", "--no-update", timeout=120
    ).expect_ok()

    retired = project / ".agent" / "ai-hats" / "library" / "hooks"
    assert not retired.exists(), f"retired library/hooks/ still created: {retired}"

    tmp_venv_project.run(
        "self", "init", "-r", "hypothesis-intake", "-p", "claude", "--no-update", timeout=120
    ).expect_ok()

    assert not retired.exists(), f"retired library/hooks/ re-created on role switch: {retired}"
