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


# --- HATS-055: extras round-trip + dropped resolution fix ---


def test_task_card_unknown_field_captured_into_extras():
    data = {
        "id": "T-1",
        "title": "Test",
        "acceptance_criteria": ["ac one", "ac two"],
        "custom_field": {"nested": True},
    }
    card = TaskCard.from_dict(data)
    assert card.extras == {
        "acceptance_criteria": ["ac one", "ac two"],
        "custom_field": {"nested": True},
    }


def test_task_card_extras_round_trip_via_to_dict():
    data = {
        "id": "T-1",
        "title": "Test",
        "acceptance_criteria": ["ac one", "ac two"],
    }
    card = TaskCard.from_dict(data)
    out = card.to_dict()
    assert out["acceptance_criteria"] == ["ac one", "ac two"]


def test_task_card_extras_survive_state_transition():
    """Regression for HATS-055: transition must not drop unknown fields."""
    data = {
        "id": "T-1",
        "title": "Test",
        "state": "brainstorm",
        "acceptance_criteria": ["must work after transition"],
        "custom_field": "value",
    }
    card = TaskCard.from_dict(data)
    card.transition_to(TaskState.PLAN)
    card.transition_to(TaskState.EXECUTE)
    out = card.to_dict()
    assert out["state"] == "execute"
    assert out["acceptance_criteria"] == ["must work after transition"]
    assert out["custom_field"] == "value"


def test_task_card_extras_survive_full_yaml_round_trip(tmp_path):
    """End-to-end: from_yaml + save + from_yaml preserves unknown fields."""
    import yaml

    src = tmp_path / "task.yaml"
    src.write_text(yaml.safe_dump({
        "id": "T-1",
        "title": "Test",
        "state": "plan",
        "acceptance_criteria": ["a", "b", "c"],
        "weird_field": [1, 2, {"k": "v"}],
    }))

    card = TaskCard.from_yaml(src)
    card.transition_to(TaskState.EXECUTE)
    card.save(src)

    reloaded = TaskCard.from_yaml(src)
    assert reloaded.state == TaskState.EXECUTE
    assert reloaded.extras["acceptance_criteria"] == ["a", "b", "c"]
    assert reloaded.extras["weird_field"] == [1, 2, {"k": "v"}]


def test_task_card_resolution_field_no_longer_dropped():
    """Regression: 'resolution' field was declared but never serialized."""
    data = {
        "id": "T-1",
        "title": "Test",
        "resolution": "closed: superseded by HATS-100",
    }
    card = TaskCard.from_dict(data)
    assert card.resolution == "closed: superseded by HATS-100"
    out = card.to_dict()
    assert out["resolution"] == "closed: superseded by HATS-100"


def test_task_card_empty_resolution_omitted_from_output():
    card = TaskCard.from_dict({"id": "T-1", "title": "Test"})
    out = card.to_dict()
    assert "resolution" not in out


def test_task_card_empty_extras_not_in_output():
    card = TaskCard.from_dict({"id": "T-1", "title": "Test"})
    out = card.to_dict()
    assert "extras" not in out


def test_task_card_extras_cannot_shadow_known_field_via_to_dict():
    """Defensive: even if user mutates extras to contain a known key,
    the typed field wins in to_dict output."""
    card = TaskCard(id="T-1", title="Real Title")
    card.extras["title"] = "IMPOSTER"
    out = card.to_dict()
    assert out["title"] == "Real Title"


def test_task_card_known_fields_not_double_captured():
    """from_dict should NOT put known keys into extras."""
    data = {
        "id": "T-1",
        "title": "Test",
        "state": "plan",
        "priority": "high",
        "tags": ["bug"],
        "extra1": "in extras",
    }
    card = TaskCard.from_dict(data)
    assert "id" not in card.extras
    assert "state" not in card.extras
    assert "tags" not in card.extras
    assert card.extras == {"extra1": "in extras"}


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
