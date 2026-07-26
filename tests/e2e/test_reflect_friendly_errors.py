"""E2E: ``ai-hats reflect *`` renders a config error, not a traceback.

History (HATS-1228): friendly rendering of the compose-seam errors was per-site
opt-in — each launch surface caught ``RoleNotFoundError`` /
``UnknownProviderError`` / ``MissingProviderError`` and called its handler. Four
surfaces did; ``cli/reflect.py`` composes in FIVE places and did not, so every
``reflect`` subcommand exited 1 on a traceback. ``_handle_role_not_found``'s
docstring had claimed ``ai-hats reflect *`` as a covered surface since HATS-547 —
it never was. The fix moves dispatch to the root click group, so a surface cannot
opt out by omission; ``reflect.py`` itself is not edited.

Why an emptied ``provider:`` is the probe: it needs no role argument, no session
data and no provider binary, and it fires inside ``build_composition_payload``
before any runner spawns — so every ``reflect`` subcommand can be driven the same
way in a non-TTY subprocess. The unknown-role and unknown-provider siblings ride
the same dispatch and are pinned on the launch surfaces by
``test_unknown_role_friendly_error.py`` / ``test_unknown_provider_friendly_error.py``.

Setup contract (real subprocess + real ``ai-hats`` binary — satisfies
``dev_rule_e2e_gate`` for changes under ``src/ai_hats/cli/``): ``tmp_project``
bootstraps with ``provider: claude``; this file rewrites ai-hats.yaml with an
empty provider.

Fail-under-revert: dropping the ``invoke`` override on the root group re-leaks
the traceback on every parametrized subcommand — nothing in ``reflect.py``
catches these errors.

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
        pytest.param(("reflect", "all"), id="reflect-all"),
        pytest.param(("reflect", "role", "maintainer"), id="reflect-role"),
        pytest.param(("reflect", "roles"), id="reflect-roles"),
    ],
)
def test_e2e_reflect_reports_config_error_without_traceback(
    provider_less_project, argv,
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
            f"stderr missing marker {marker!r}\n"
            f"stderr (tail 800):\n{result.stderr[-800:]}"
        )

    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"traceback leaked to user-facing output:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
