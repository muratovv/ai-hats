"""E2E sentinel: materialization under a second role must not narrow project hook surface (ADR-0021 M5).

RED under current behavior (HATS-1480):
Materializing a narrow role after a wide one sweeps the wide role's skill runtime
hooks from library/hooks/. HATS-1480 **rewrites** this test rather than un-xfailing
it — deleting the directory makes the precondition below unsatisfiable, so the test
would fail on it instead of turning green.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.xfail(
    strict=True,
    reason="HATS-1480 — library/hooks/ переписывается из композиции текущей роли; узкая роль сносит хуки широкой (M5, M8)",
)
def test_role_switch_does_not_narrow_hooks(tmp_venv_project) -> None:
    """ADR-0021 M5 | RED-xfail | HATS-1480 rewrites this test when the copy goes."""
    project = tmp_venv_project.path

    # Step 1: Init with wide role (maintainer)
    tmp_venv_project.run(
        "self", "init", "-r", "maintainer", "-p", "claude", "--no-update", timeout=120
    ).expect_ok()

    hooks_dir = project / ".agent" / "ai-hats" / "library" / "hooks"
    assert hooks_dir.is_dir(), f"hooks dir does not exist after init: {hooks_dir}"

    hooks_a = {p.name for p in hooks_dir.iterdir() if p.is_file()}

    # Precondition assertion: maintainer role must materialize at least one per-skill hook script
    # (name with hyphen, e.g. <skill>-<basename>)
    skill_hooks_a = {name for name in hooks_a if "-" in name}
    assert skill_hooks_a, (
        f"precondition failed: maintainer role materialized no per-skill hooks in {hooks_dir}. "
        f"Found: {sorted(hooks_a)}"
    )

    # Step 2: Init with narrow role (hypothesis-intake)
    tmp_venv_project.run(
        "self", "init", "-r", "hypothesis-intake", "-p", "claude", "--no-update", timeout=120
    ).expect_ok()

    hooks_b = {p.name for p in hooks_dir.iterdir() if p.is_file()}

    missing = hooks_a - hooks_b
    assert not missing, (
        f"role switch narrowed project hook surface in {hooks_dir}. "
        f"Missing hooks previously present in maintainer: {sorted(missing)}"
    )
