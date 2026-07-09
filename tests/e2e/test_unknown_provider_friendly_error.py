"""E2E: bare ``ai-hats -p <bogus>`` exits clean, not on a raw traceback.

History (HATS-965): the interactive bare-launch surface had friendly handling
for an unknown ``--role`` (``RoleNotFoundError`` -> ``_handle_role_not_found``
-> exit 2) but NONE for an unknown ``--provider``. ``get_provider`` raised a
bare ``ValueError`` that escaped ``_launch_session`` as an uncaught traceback.
The fix mirrors the role pattern: a typed ``UnknownProviderError`` +
``cli/_helpers._handle_unknown_provider``.

Setup contract (real subprocess + real ``ai-hats`` binary — satisfies
``dev_rule_e2e_gate`` for changes under ``src/ai_hats/cli/``):

1. ``tmp_project`` bootstraps a project whose built-in library ships the
   ``maintainer`` role. We pass a VALID role so role validation/composition
   (which runs BEFORE the provider check) passes and the raise fires on the
   provider — not on the role.
2. ``ai-hats -p <bogus> --role maintainer``. Only the bare-launch surface
   honours ``-p``; the raise fires in ``build_composition_payload`` BEFORE
   ``WrapRunner`` PTY-attaches, so this runs cleanly in a non-TTY subprocess
   (no provider binary is ever spawned).
3. Assertions:
   - exit code == 2 (Click's UsageError convention; mirrors the role handler).
   - stderr names the bogus provider.
   - stderr contains the ``Available providers:`` header.
   - stderr lists a known shipped provider (``claude``).
   - stderr hints at ``ai-hats list providers``.
   - combined stdout+stderr does NOT contain ``Traceback``.

Fail-under-revert: removing the ``except UnknownProviderError`` arm in
``cli/__init__.py:_launch_session`` re-leaks the bare ``ValueError`` traceback
and fails this test.

Deliberate long e2e scenario contract — noqa: comment-length.
"""

from __future__ import annotations

import pytest


# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke]


_BOGUS = "definitely-not-a-real-provider"


def test_e2e_unknown_provider_exits_clean_with_provider_list(tmp_project) -> None:
    """``ai-hats -p <bogus> --role maintainer`` → exit 2, friendly, no traceback."""
    result = tmp_project.run("-p", _BOGUS, "--role", "maintainer", timeout=10.0)

    # Exit 2 is Click's UsageError convention; mirrors the unknown-role handler.
    assert result.exit_code == 2, (
        f"expected exit 2, got {result.exit_code}\n"
        f"stdout (tail 500):\n{result.stdout[-500:]}\n"
        f"stderr (tail 500):\n{result.stderr[-500:]}"
    )

    # Names the bogus provider + heads the available-providers list + carries at
    # least one shipped provider (``claude`` — a built-in since the registry
    # existed) + hints at the real ``ai-hats list providers`` subcommand.
    for marker in (
        _BOGUS,
        "Available providers:",
        "claude",
        "ai-hats list providers",
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
