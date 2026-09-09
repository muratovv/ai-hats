"""e2e (HATS-1218)

flow:   a developer specifying provider override flag -p on batch execution commands
cmds:
    ai-hats execute -r maintainer --batch -p definitely-not-a-real-provider
expect: provider flag -p is respected in batch mode and produces clean error for invalid
        providers
why:    without batch provider overrides, batch commands ignore -p flags and default to
        configured provider
"""

from __future__ import annotations

import pytest


# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke, pytest.mark.surfaces]


_BOGUS = "definitely-not-a-real-provider"


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(
            ("execute", "-r", "maintainer", "--batch", "-p", _BOGUS),
            id="execute--batch",
        ),
        pytest.param(
            ("agent", "maintainer", "--task", "ping", "-p", _BOGUS),
            id="agent",
        ),
        pytest.param(
            ("agent", "maintainer", "--task", "ping", "-p", _BOGUS, "--dry-run"),
            id="agent--dry-run",
        ),
    ],
)
def test_e2e_batch_surface_resolves_the_requested_provider(tmp_project, argv) -> None:
    """``-p <bogus>`` reaches provider resolution and exits 2, friendly."""
    result = tmp_project.run(*argv, timeout=30.0)

    assert result.exit_code == 2, (
        f"expected exit 2, got {result.exit_code}\n"
        f"stdout (tail 500):\n{result.stdout[-500:]}\n"
        f"stderr (tail 500):\n{result.stderr[-500:]}"
    )

    # Naming the bogus provider is the proof the flag was honoured: pre-fix the
    # batch path composed the CONFIGURED provider and never saw this string.
    for marker in (_BOGUS, "Available providers:", "claude", "ai-hats list providers"):
        assert marker in result.stderr, (
            f"stderr missing marker {marker!r}\nstderr (tail 800):\n{result.stderr[-800:]}"
        )

    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"traceback leaked to user-facing output:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
