"""Tests for core data models."""

import pytest

from ai_hats.models import (
    ComponentConfig,
    Composition,
    FeedbackConfig,
    FeedbackPolicy,
    HooksConfig,
    JudgeConfig,
    JudgePolicy,
    OverlayConfig,
    ProjectConfig,
    SessionRetroConfig,
    SmartThreshold,
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


def test_load_header_extracts_scalars(tmp_path):
    p = tmp_path / "task.yaml"
    p.write_text(
        "id: T-1\n"
        "title: 'Hello world'\n"
        "state: plan\n"
        "priority: high\n"
        "assignee: alice\n"
        "reviewer: bob\n"
        "role: dev\n"
        "description: 'long description here'\n"
    )
    h = TaskCard.load_header(p)
    assert h == {
        "id": "T-1",
        "title": "Hello world",
        "state": "plan",
        "priority": "high",
        "assignee": "alice",
        "reviewer": "bob",
        "role": "dev",
    }


def test_load_header_handles_quotes_and_defaults(tmp_path):
    p = tmp_path / "task.yaml"
    p.write_text(
        "id: T-2\n"
        "title: \"with: colon\"\n"
        "state: brainstorm\n"
        "assignee: ''\n"
    )
    h = TaskCard.load_header(p)
    assert h["title"] == "with: colon"
    assert h["assignee"] == ""
    assert h["priority"] == "medium"
    assert h["reviewer"] == "user"
    assert h["role"] == ""


def test_load_header_unescapes_doubled_single_quote(tmp_path):
    p = tmp_path / "task.yaml"
    p.write_text("id: T-3\nstate: done\ntitle: 'Foo''s bar'\n")
    h = TaskCard.load_header(p)
    assert h["title"] == "Foo's bar"


def test_load_header_falls_back_when_id_missing_in_regex(tmp_path):
    """Block-scalar layouts hide `id:` from the line-based regex — fall back."""
    p = tmp_path / "task.yaml"
    p.write_text(
        "title: 'block-scalar layout'\n"
        "description: |\n"
        "  multi-line description with id: T-X inside\n"
        "  more text\n"
        "state: plan\n"
        "id: T-FALLBACK\n"
        "priority: medium\n"
    )
    h = TaskCard.load_header(p)
    assert h["id"] == "T-FALLBACK"
    assert h["state"] == "plan"


def test_load_header_matches_from_yaml_on_real_layout(tmp_path):
    """load_header must agree with from_yaml for any field the renderer reads."""
    p = tmp_path / "task.yaml"
    card = TaskCard(
        id="T-99",
        title="Round-trip me",
        state=TaskState.EXECUTE,
        priority="high",
        assignee="charlie",
        reviewer="dave",
        role="architect",
    )
    card.save(p)
    full = TaskCard.from_yaml(p)
    h = TaskCard.load_header(p)
    assert h["id"] == full.id
    assert h["title"] == full.title
    assert h["state"] == full.state.value
    assert h["priority"] == full.priority
    assert h["assignee"] == full.assignee
    assert h["reviewer"] == full.reviewer
    assert h["role"] == full.role


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


# -- FeedbackConfig tests --


def test_smart_threshold_defaults():
    t = SmartThreshold()
    assert t.min_turns == 5
    assert t.min_tool_calls == 10


def test_smart_threshold_roundtrip():
    t = SmartThreshold(min_turns=15, min_tool_calls=20)
    restored = SmartThreshold.from_dict(t.to_dict())
    assert restored == t


def test_smart_threshold_from_empty():
    assert SmartThreshold.from_dict(None) == SmartThreshold()
    assert SmartThreshold.from_dict({}) == SmartThreshold()


def test_session_retro_config_defaults():
    c = SessionRetroConfig()
    assert c.policy == FeedbackPolicy.SMART
    assert c.background is True
    assert c.mode == "programmatic"


def test_session_retro_config_roundtrip():
    c = SessionRetroConfig(
        policy=FeedbackPolicy.HINT,
        smart_threshold=SmartThreshold(min_turns=10, min_tool_calls=5),
        background=False,
        mode="llm",
    )
    restored = SessionRetroConfig.from_dict(c.to_dict())
    assert restored == c


def test_judge_config_roundtrip():
    c = JudgeConfig(policy=JudgePolicy.OFF)
    restored = JudgeConfig.from_dict(c.to_dict())
    assert restored == c


def test_feedback_config_defaults():
    fc = FeedbackConfig()
    assert fc.session_retro.policy == FeedbackPolicy.SMART
    assert fc.judge.policy == JudgePolicy.MANUAL
    assert fc.is_default


def test_feedback_config_is_default_false_after_change():
    fc = FeedbackConfig()
    fc.session_retro.policy = FeedbackPolicy.OFF
    assert not fc.is_default


def test_feedback_config_roundtrip():
    fc = FeedbackConfig(
        session_retro=SessionRetroConfig(
            policy=FeedbackPolicy.ALWAYS, mode="hybrid",
        ),
        judge=JudgeConfig(policy=JudgePolicy.OFF),
    )
    restored = FeedbackConfig.from_dict(fc.to_dict())
    assert restored == fc


def test_feedback_config_from_empty():
    assert FeedbackConfig.from_dict(None) == FeedbackConfig()
    assert FeedbackConfig.from_dict({}) == FeedbackConfig()


# -- ProjectConfig v2: unified config with feedback --


def test_project_config_v2_roundtrip(tmp_path):
    config = ProjectConfig(
        provider="claude",
        active_role="assistant",
        feedback=FeedbackConfig(
            session_retro=SessionRetroConfig(policy=FeedbackPolicy.HINT, mode="llm"),
        ),
    )
    path = tmp_path / "ai-hats.yaml"
    config.save(path)

    loaded = ProjectConfig.from_yaml(path)
    assert loaded.active_role == "assistant"
    assert loaded.provider == "claude"
    assert loaded.feedback.session_retro.policy == FeedbackPolicy.HINT
    assert loaded.feedback.session_retro.mode == "llm"
    assert loaded.schema_version == 2


def test_project_config_v2_default_feedback_not_serialized(tmp_path):
    config = ProjectConfig(provider="claude", active_role="assistant")
    path = tmp_path / "ai-hats.yaml"
    config.save(path)

    content = path.read_text()
    assert "feedback" not in content


def test_project_config_v2_non_default_feedback_serialized(tmp_path):
    config = ProjectConfig(
        provider="claude",
        feedback=FeedbackConfig(
            session_retro=SessionRetroConfig(policy=FeedbackPolicy.OFF),
        ),
    )
    path = tmp_path / "ai-hats.yaml"
    config.save(path)

    import yaml as _yaml
    data = _yaml.safe_load(path.read_text())
    assert "feedback" in data
    assert data["feedback"]["session_retro"]["policy"] == "off"


# -- Migration v1 → v2 --


def test_migration_v1_to_v2_merges_profile(tmp_path):
    """v1 ai-hats.yaml + profile.json → merged v2 ai-hats.yaml."""
    import json

    yaml_path = tmp_path / "ai-hats.yaml"
    yaml_path.write_text("provider: gemini\ndefault_role: sre\nschema_version: 1\n")

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps({
        "active_role": "assistant",
        "provider": "claude",
        "feedback": {
            "session_retro": {"policy": "hint", "mode": "llm"},
            "judge": {"policy": "manual"},
        },
    }))

    config = ProjectConfig.from_yaml(yaml_path)
    assert config.schema_version == 2
    assert config.active_role == "assistant"
    assert config.provider == "claude"  # profile wins
    assert config.default_role == "sre"  # preserved from yaml
    assert config.feedback.session_retro.policy == FeedbackPolicy.HINT
    # profile.json renamed to .bak
    assert not profile_path.exists()
    assert profile_path.with_suffix(".json.bak").exists()


def test_migration_v1_without_profile(tmp_path):
    """v1 ai-hats.yaml without profile.json still migrates to v2."""
    yaml_path = tmp_path / "ai-hats.yaml"
    yaml_path.write_text("provider: claude\ndefault_role: go-dev\nschema_version: 1\n")

    config = ProjectConfig.from_yaml(yaml_path)
    assert config.schema_version == 2
    assert config.provider == "claude"
    assert config.active_role == ""
    assert config.feedback.is_default


def test_migration_idempotent(tmp_path):
    """Loading a v2 config does not re-migrate."""
    config = ProjectConfig(provider="claude", active_role="sre")
    path = tmp_path / "ai-hats.yaml"
    config.save(path)

    loaded = ProjectConfig.from_yaml(path)
    assert loaded.schema_version == 2
    assert loaded.active_role == "sre"


