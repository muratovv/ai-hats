"""E2E: ``ai-hats --role <bogus>`` exits clean with available-roles list.

Before HATS-507 the unknown-role path raised a bare ``RuntimeError`` from
inside the ``compose_role`` pipeline step. Click had no special handling
for it, so users saw a 9-frame Python traceback and an exit code from
Python's unhandled-exception path. No discoverability for the typo.

Setup contract (real subprocess + real ``ai-hats`` binary):

1. ``tmp_project`` fixture bootstraps a role-less project pointed at the
   dev-venv ``ai-hats`` binary.
2. We invoke ``ai-hats --role <bogus>`` via :class:`Project.run`.
3. Assertions:
   - exit code == 2 (Click's UsageError convention)
   - stderr names the bogus role
   - stderr contains the ``Available roles:`` header
   - stderr lists at least one known shipped role (``maintainer``)
   - combined stdout+stderr does NOT contain ``Traceback``

Fail-under-revert: removing EITHER the typed raise in
``pipeline/steps/compose.py`` OR the ``try/except RoleNotFoundError`` in
``cli/__init__.py`` makes the ``Traceback`` assertion fail (the bare
``RuntimeError`` bubbles through Click and prints a traceback in both
revert cases).
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


def test_e2e_unknown_role_exits_clean_with_role_list(tmp_project) -> None:
    """``ai-hats --role <bogus>`` → exit 2, friendly message, no traceback."""
    result = tmp_project.run("--role", "definitely-not-a-real-role")

    # Exit 2 is Click's UsageError convention; HATS-507 mirrors it.
    assert result.exit_code == 2, (
        f"expected exit 2, got {result.exit_code}\n"
        f"stdout (tail 500):\n{result.stdout[-500:]}\n"
        f"stderr (tail 500):\n{result.stderr[-500:]}"
    )

    # Names the bogus role + heads the available-roles list + carries
    # at least one shipped role name. We pick ``maintainer`` because it
    # is a stable usage-tier role that has shipped since HATS-433.
    for marker in (
        "definitely-not-a-real-role",
        "Available roles:",
        "maintainer",
        "ai-hats list roles",
    ):
        assert marker in result.stderr, (
            f"stderr missing marker {marker!r}\n"
            f"stderr (tail 800):\n{result.stderr[-800:]}"
        )

    # No traceback leak in either stream — the whole point of the change.
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"traceback leaked to user-facing output:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
