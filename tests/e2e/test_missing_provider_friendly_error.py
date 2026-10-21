"""e2e (HATS-1224)

flow:   a developer specifying a provider name whose package is not installed
cmds:
    ai-hats -p missing-provider --role assistant
expect: CLI exits cleanly with code 2 displaying friendly remediation instructions
        without traceback
why: without friendly provider error handling, uninstalled provider packages throw raw
     ImportErrors"""

from __future__ import annotations

import pytest


# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke, pytest.mark.surfaces]


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
