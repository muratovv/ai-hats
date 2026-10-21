"""e2e (HATS-547, HATS-1228)

flow:   a developer running reflect subcommands when project configuration has invalid
        provider
cmds:
    ai-hats reflect all
expect: command exits cleanly with exit code 2 and friendly error message instead of raw
        traceback
why:    without root Click error handling, reflect subcommands leak uncaught Python
        tracebacks on config errors
"""

from __future__ import annotations

import pytest


# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke, pytest.mark.observe]


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
        pytest.param(("reflect", "all"), id="reflect-all"),
        pytest.param(("reflect", "role", "maintainer"), id="reflect-role"),
        pytest.param(("reflect", "roles"), id="reflect-roles"),
        pytest.param(("reflect", "issue", "something broken"), id="reflect-issue"),
    ],
)
def test_e2e_reflect_reports_config_error_without_traceback(
    provider_less_project,
    argv,
) -> None:
    """Every ``reflect`` subcommand that composes → exit 2, friendly."""
    result = provider_less_project.run(*argv, timeout=30.0)

    assert result.exit_code == 2, (
        f"expected exit 2, got {result.exit_code}\n"
        f"stdout (tail 500):\n{result.stdout[-500:]}\n"
        f"stderr (tail 500):\n{result.stderr[-500:]}"
    )

    for marker in ("no provider configured", "ai-hats config set -p"):
        assert marker in result.stderr, (
            f"stderr missing marker {marker!r}\nstderr (tail 800):\n{result.stderr[-800:]}"
        )

    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"traceback leaked to user-facing output:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    hyp_dir = (
        provider_less_project.path / ".agent" / "ai-hats" / "tracker" / "backlog" / "hypotheses"
    )
    created_hyps = list(hyp_dir.glob("HYP-*.yaml")) if hyp_dir.exists() else []
    assert not created_hyps, f"expected no HYP cards created on error, found: {created_hyps}"
