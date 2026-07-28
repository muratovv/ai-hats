"""E2E: the batch surfaces honour ``-p``, and say so when it is wrong.

History (HATS-1218): ``build_composition_payload`` hard-read ``cfg.provider``
whenever ``interactive=False``. ``ai-hats execute`` declared ``--provider`` and
``--batch`` side by side, so ``execute -p X --batch`` accepted the flag and ran
the configured surface in silence; ``ai-hats agent`` had no ``-p`` at all.

Why a *bogus* provider name is the probe: it makes the assertion hermetic. The
raise fires in ``build_composition_payload`` before any runner spawns, so the
test needs no provider binary, no auth and no network — while still proving the
flag reached provider resolution, which is the whole claim. A positive
"``-p`` selects surface X" case needs a second real surface and lives at the
pipeline-integration layer instead (``tests/pipeline/test_provider_override.py``,
which registers a stub provider).

Setup contract (real subprocess + real ``ai-hats`` binary — satisfies
``dev_rule_e2e_gate`` for changes under ``src/ai_hats/cli/``): ``tmp_project``
bootstraps a project configured with ``provider: claude`` whose built-in library
ships the ``maintainer`` role, so a VALID role is passed and the raise lands on
the provider rather than the role.

Fail-under-revert:

- ``execute --batch``: pre-fix the flag is dropped, ``claude`` composes, and no
  message ever names the bogus provider.
- ``agent``: pre-fix Click rejects the unknown ``-p`` option, so stderr carries
  Click's usage error instead of the provider list.

Deliberate long e2e scenario contract — noqa: comment-length.
"""

from __future__ import annotations

import pytest


# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke]


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
