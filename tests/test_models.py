"""Tests for core data models."""

import pytest

from ai_hats.models import (
    ComponentConfig,
    Composition,
    HooksConfig,
    OverlayConfig,
    ProjectConfig,
    TaskCard,
    TaskState,
    resolve_namespace,
)


def test_resolve_namespace():
    assert resolve_namespace("dev::python") == "dev/python"
    assert resolve_namespace("trait-base") == "trait-base"
    assert resolve_namespace("a::b::c") == "a/b/c"


def test_task_state_valid_transitions():
    assert TaskState.BRAINSTORM.can_transition_to(TaskState.PLAN)
    assert TaskState.PLAN.can_transition_to(TaskState.EXECUTE)
    assert TaskState.EXECUTE.can_transition_to(TaskState.DOCUMENT)
    assert TaskState.DOCUMENT.can_transition_to(TaskState.REVIEW)
    assert TaskState.REVIEW.can_transition_to(TaskState.DONE)


def test_task_state_invalid_transitions():
    assert not TaskState.BRAINSTORM.can_transition_to(TaskState.EXECUTE)
    assert not TaskState.BRAINSTORM.can_transition_to(TaskState.DONE)
    assert not TaskState.EXECUTE.can_transition_to(TaskState.REVIEW)
    assert not TaskState.REVIEW.can_transition_to(TaskState.BRAINSTORM)
    assert not TaskState.DONE.can_transition_to(TaskState.BRAINSTORM)


def test_task_state_blocked_recovery():
    assert TaskState.BLOCKED.can_transition_to(TaskState.BRAINSTORM)
    assert TaskState.BLOCKED.can_transition_to(TaskState.PLAN)
    assert TaskState.BLOCKED.can_transition_to(TaskState.EXECUTE)
    assert TaskState.BLOCKED.can_transition_to(TaskState.DOCUMENT)


def test_task_state_failed_recovery():
    assert TaskState.FAILED.can_transition_to(TaskState.BRAINSTORM)
    assert not TaskState.FAILED.can_transition_to(TaskState.EXECUTE)


def test_task_card_transition():
    task = TaskCard(id="T-1", title="Test")
    task.transition_to(TaskState.PLAN)
    assert task.state == TaskState.PLAN

    task.transition_to(TaskState.EXECUTE)
    assert task.state == TaskState.EXECUTE


def test_task_card_invalid_transition():
    task = TaskCard(id="T-1", title="Test")
    with pytest.raises(ValueError, match="Invalid transition"):
        task.transition_to(TaskState.DONE)


def test_composition_from_dict():
    data = {
        "traits": ["trait-base"],
        "rules": ["dev_rule_git"],
        "skills": ["backlog-manager"],
        "hooks": {"session_start": ["scripts/start.sh"]},
        "mcp": [{"name": "review-server", "config": "mcp/review.json"}],
    }
    comp = Composition.from_dict(data)
    assert comp.traits == ["trait-base"]
    assert comp.rules == ["dev_rule_git"]
    assert comp.skills == ["backlog-manager"]
    assert comp.hooks.session_start == ["scripts/start.sh"]
    assert len(comp.mcp) == 1
    assert comp.mcp[0].name == "review-server"


def test_composition_from_empty():
    comp = Composition.from_dict(None)
    assert comp.traits == []
    assert comp.rules == []


def test_hooks_config_get_scripts():
    hooks = HooksConfig(session_start=["a.sh", "b.sh"])
    from ai_hats.models import LifecycleEvent
    assert hooks.get_scripts(LifecycleEvent.SESSION_START) == ["a.sh", "b.sh"]
    assert hooks.get_scripts(LifecycleEvent.SESSION_END) == []


def test_component_config_from_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
name: test-role
priorities:
  - Safety
  - Speed
composition:
  traits:
    - trait-base
  rules:
    - dev_rule_git
injection: |
  # Test injection
""")
    config = ComponentConfig.from_yaml(config_file)
    assert config.name == "test-role"
    assert config.priorities == ["Safety", "Speed"]
    assert config.composition.traits == ["trait-base"]
    assert "Test injection" in config.injection


# -- OverlayConfig tests --


def test_overlay_config_from_dict():
    data = {
        "add": {"traits": ["my-trait"], "skills": ["my-skill"]},
        "remove": {"traits": ["old-trait"], "rules": ["old-rule"]},
        "injection_append": "Extra text.",
    }
    overlay = OverlayConfig.from_dict(data)
    assert overlay.add_traits == ["my-trait"]
    assert overlay.add_skills == ["my-skill"]
    assert overlay.remove_traits == ["old-trait"]
    assert overlay.remove_rules == ["old-rule"]
    assert overlay.injection_append == "Extra text."
    assert overlay.add_rules == []
    assert overlay.remove_skills == []


def test_overlay_config_from_empty():
    overlay = OverlayConfig.from_dict(None)
    assert overlay.is_empty
    overlay2 = OverlayConfig.from_dict({})
    assert overlay2.is_empty


def test_overlay_config_roundtrip():
    original = OverlayConfig(
        add_traits=["t1"], remove_skills=["s1"], injection_append="text",
    )
    d = original.to_dict()
    restored = OverlayConfig.from_dict(d)
    assert restored.add_traits == original.add_traits
    assert restored.remove_skills == original.remove_skills
    assert restored.injection_append == original.injection_append


def test_overlay_config_to_dict_omits_empty():
    overlay = OverlayConfig(add_traits=["t1"])
    d = overlay.to_dict()
    assert "add" in d
    assert "remove" not in d
    assert "injection_append" not in d


def test_overlay_config_is_empty():
    assert OverlayConfig().is_empty
    assert not OverlayConfig(add_traits=["x"]).is_empty
    assert not OverlayConfig(injection_append="x").is_empty


# -- ProjectConfig with customizations --


def test_project_config_customizations_roundtrip(tmp_path):
    config = ProjectConfig(
        provider="claude",
        default_role="sre",
        customizations={
            "sre": OverlayConfig(add_traits=["my-trait"], remove_skills=["old-skill"]),
        },
    )
    path = tmp_path / "ai-hats.yaml"
    config.save(path)

    loaded = ProjectConfig.from_yaml(path)
    assert "sre" in loaded.customizations
    assert loaded.customizations["sre"].add_traits == ["my-trait"]
    assert loaded.customizations["sre"].remove_skills == ["old-skill"]


def test_project_config_no_customizations_backward_compat(tmp_path):
    """Existing ai-hats.yaml without customizations field should load fine."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text("provider: claude\ndefault_role: assistant\nschema_version: 1\n")

    config = ProjectConfig.from_yaml(path)
    assert config.customizations == {}
    assert config.provider == "claude"


def test_project_config_empty_customizations_not_serialized(tmp_path):
    """Empty customizations should not appear in saved YAML."""
    config = ProjectConfig(provider="claude", customizations={})
    path = tmp_path / "ai-hats.yaml"
    config.save(path)

    content = path.read_text()
    assert "customizations" not in content
