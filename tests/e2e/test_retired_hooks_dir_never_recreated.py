"""E2E sentinel: the retired ``library/hooks/`` directory is never re-created (HATS-1480 AC-1).

This does NOT guard ADR-0021 M5. It replaced the M5 sentinel
``test_role_switch_does_not_narrow_hooks``, whose precondition (skill scripts
inside ``library/hooks/``) became unsatisfiable once the directory went away.
M5 must be asserted on the session-tree address instead — owed by HATS-1500.
Do not rename this back: it asserts absence, not non-narrowing.

Fail-under-revert: put ``_lib_hooks_dir`` back into ``init``'s mkdir tuple.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


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
