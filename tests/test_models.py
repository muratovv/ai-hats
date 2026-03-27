"""Tests for core data models."""

import pytest

from ai_hats.models import (
    ComponentConfig,
    Composition,
    HooksConfig,
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
    assert TaskState.EXECUTE.can_transition_to(TaskState.REVIEW)
    assert TaskState.REVIEW.can_transition_to(TaskState.DONE)


def test_task_state_invalid_transitions():
    assert not TaskState.BRAINSTORM.can_transition_to(TaskState.EXECUTE)
    assert not TaskState.BRAINSTORM.can_transition_to(TaskState.DONE)
    assert not TaskState.REVIEW.can_transition_to(TaskState.BRAINSTORM)
    assert not TaskState.DONE.can_transition_to(TaskState.BRAINSTORM)


def test_task_state_blocked_recovery():
    assert TaskState.BLOCKED.can_transition_to(TaskState.BRAINSTORM)
    assert TaskState.BLOCKED.can_transition_to(TaskState.PLAN)
    assert TaskState.BLOCKED.can_transition_to(TaskState.EXECUTE)


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
