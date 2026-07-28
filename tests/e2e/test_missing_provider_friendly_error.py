"""E2E: an empty ``provider:`` in ai-hats.yaml exits clean, not on a traceback.

History (HATS-1224): ``_effective_provider`` raised a bare ``RuntimeError`` when
no provider was configured, and no CLI arm caught it — every launch surface
leaked a 9-frame traceback. Its sibling failure (an *unknown* provider name) had
exited 2 with a friendly list since HATS-965/HATS-1218, so two adjacent
provider-config errors behaved differently. The fix mirrors that pattern: a typed
``composition_seam.MissingProviderError`` + ``cli/_helpers._handle_missing_provider``.

Why an emptied ``provider:`` is the probe: ``ProjectConfig.provider`` defaults to
``claude``, so only an explicit empty value reaches the raise. ``-p ""`` cannot —
it is falsy and falls through to the config. The raise fires inside the compose
seam before any runner spawns, so this needs no provider binary, no auth, no
network, and runs cleanly in a non-TTY subprocess.

Setup contract (real subprocess + real ``ai-hats`` binary — satisfies
``dev_rule_e2e_gate`` for changes under ``src/ai_hats/cli/``): ``tmp_project``
bootstraps with ``provider: claude``; this file rewrites ai-hats.yaml with an
empty provider. A VALID role (``maintainer``, shipped by the built-in library) is
passed on every surface so role validation — which runs BEFORE the provider check
— cannot be what fails.

Surfaces cover both seam functions: ``build_composition_payload`` (bare launch,
``execute --batch``, ``agent``) and ``build_preview_payload`` (``--dry-run`` on
both the HITL and the automate side).

Fail-under-revert: dropping the ``except MissingProviderError`` arm at any one
CLI site re-leaks that surface's traceback and fails its parametrized case.

Deliberate long e2e scenario contract — noqa: comment-length.
"""

from __future__ import annotations

import pytest


# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke]


@pytest.fixture
def provider_less_project(tmp_project):
    """``tmp_project`` with ``provider:`` explicitly emptied in ai-hats.yaml."""
    from ai_hats.config.project import ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG

    config_path = tmp_project.path / PROJECT_CONFIG
    cfg = ProjectConfig.from_yaml(config_path)
    cfg.provider = ""
    cfg.save(config_path)
    return tmp_project


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(("--role", "maintainer"), id="bare-launch"),
        pytest.param(("--dry-run", "--role", "maintainer"), id="bare--dry-run"),
        pytest.param(
            ("execute", "-r", "maintainer", "--prompt", "ping", "--batch"),
            id="execute--batch",
        ),
        pytest.param(("agent", "maintainer", "--task", "ping"), id="agent"),
        pytest.param(
            ("agent", "maintainer", "--task", "ping", "--dry-run"),
            id="agent--dry-run",
        ),
    ],
)
def test_e2e_missing_provider_exits_clean_with_remediation(
    provider_less_project,
    argv,
) -> None:
    """Every launch/preview surface → exit 2, friendly, no traceback."""
    result = provider_less_project.run(*argv, timeout=30.0)

    # Exit 2 is Click's UsageError convention; mirrors the unknown-provider handler.
    assert result.exit_code == 2, (
        f"expected exit 2, got {result.exit_code}\n"
        f"stdout (tail 500):\n{result.stdout[-500:]}\n"
        f"stderr (tail 500):\n{result.stderr[-500:]}"
    )

    # Names the condition + the exact remediation command + the available set,
    # so the user never has to guess which provider to name.
    for marker in (
        "no provider configured",
        "ai-hats config set -p",
        "Available providers:",
        "claude",
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
