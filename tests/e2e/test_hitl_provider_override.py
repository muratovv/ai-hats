"""e2e (HATS-1745)

flow:   a developer launches one HITL session with a provider override and no role flag
cmds:
    ai-hats -p holdfast
expect: the override launches holdfast while ai-hats.yaml keeps provider: claude
why:    a runtime selection must not become the project's persisted default
"""

from __future__ import annotations

import yaml
import pytest
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

from _helpers.fake_surface import SURFACE, install

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("role_override", "initial_active_role", "expected_active_role"),
    [
        (None, "", "assistant"),
        (None, "assistant", "assistant"),
        ("assistant", "", ""),
        ("assistant", "assistant", "assistant"),
    ],
)
def test_hitl_provider_override_does_not_replace_project_default(
    tmp_project,
    tmp_path,
    repo_root,
    role_override,
    initial_active_role,
    expected_active_role,
) -> None:
    config_path = tmp_project.path / PROJECT_CONFIG
    config = ProjectConfig.from_yaml(config_path)
    config.default_role = "assistant"
    config.active_role = initial_active_role
    config.save(config_path)
    surface = install(tmp_project, tmp_path, repo_root)

    surface.run_once(role=role_override)

    persisted = yaml.safe_load(config_path.read_text())
    assert persisted["provider"] == "claude"
    assert persisted["active_role"] == expected_active_role
    assert SURFACE != persisted["provider"]
