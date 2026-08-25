"""e2e (HATS-965, HATS-1218)

flow:   a developer specifying an unknown provider name on CLI
cmds:
    ai-hats -p definitely-not-a-real-provider --role maintainer
expect: CLI exits with code 2 listing available providers without printing raw Python
        traceback
why:    without provider error handling, typos in provider flags leak unhandled
        ValueErrors to terminal
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
            f"stderr missing marker {marker!r}\nstderr (tail 800):\n{result.stderr[-800:]}"
        )

    # No traceback leak in either stream — the whole point of the change.
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"traceback leaked to user-facing output:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
