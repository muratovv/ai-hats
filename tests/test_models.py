"""Tests for core data models."""

import pytest

from ai_hats.models import (
    Attachment,
    ComponentConfig,
    Composition,
    FeedbackConfig,
    FeedbackPolicy,
    HooksConfig,
    OverlayConfig,
    ProjectConfig,
    ProjectConfigError,
    SessionRetroConfig,
    SkillMetadata,
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
    src.write_text(
        yaml.safe_dump(
            {
                "id": "T-1",
                "title": "Test",
                "state": "plan",
                "acceptance_criteria": ["a", "b", "c"],
                "weird_field": [1, 2, {"k": "v"}],
            }
        )
    )

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
    p.write_text("id: T-2\ntitle: \"with: colon\"\nstate: brainstorm\nassignee: ''\n")
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
    }
    comp = Composition.from_dict(data)
    assert comp.traits == ["trait-base"]
    assert comp.rules == ["dev_rule_git"]
    assert comp.skills == ["backlog-manager"]
    assert comp.hooks.session_start == ["scripts/start.sh"]


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
        add_traits=["t1"],
        remove_skills=["s1"],
        injection_append="text",
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


# -- ProjectConfig schema validation (HATS-126) --


def test_project_config_rejects_unknown_top_level_key(tmp_path):
    path = tmp_path / "ai-hats.yaml"
    path.write_text("provider: claude\nschema_version: 2\nmystery_flag: true\n")

    with pytest.raises(ProjectConfigError) as exc:
        ProjectConfig.from_yaml(path)
    msg = str(exc.value)
    assert str(path) in msg
    assert "mystery_flag" in msg


def test_project_config_rejects_unknown_provider(tmp_path):
    path = tmp_path / "ai-hats.yaml"
    path.write_text("provider: gemini-2.5\nschema_version: 2\n")

    with pytest.raises(ProjectConfigError) as exc:
        ProjectConfig.from_yaml(path)
    msg = str(exc.value)
    assert "gemini-2.5" in msg
    # allowed list must be visible so the user can self-correct
    assert "gemini" in msg
    assert "claude" in msg


def test_project_config_valid_load_still_works(tmp_path):
    """Regression: a clean ai-hats.yaml continues to load without error."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text("provider: claude\nactive_role: assistant\nschema_version: 2\n")

    config = ProjectConfig.from_yaml(path)
    assert config.provider == "claude"
    assert config.active_role == "assistant"


# -- HATS-334: ProjectConfig.venv_path round-trip + validation --


def test_project_config_venv_path_relative_roundtrip(tmp_path):
    config = ProjectConfig(provider="claude", venv_path=".venv")
    path = tmp_path / "ai-hats.yaml"
    config.save(path)

    loaded = ProjectConfig.from_yaml(path)
    assert loaded.venv_path == ".venv"


def test_project_config_venv_path_absolute_roundtrip(tmp_path):
    config = ProjectConfig(provider="claude", venv_path="/opt/myvenv")
    path = tmp_path / "ai-hats.yaml"
    config.save(path)

    loaded = ProjectConfig.from_yaml(path)
    assert loaded.venv_path == "/opt/myvenv"


def test_project_config_venv_path_omitted_when_none(tmp_path):
    """venv_path is opt-in — saved yaml stays clean when field unused."""
    config = ProjectConfig(provider="claude")
    path = tmp_path / "ai-hats.yaml"
    config.save(path)

    assert "venv_path" not in path.read_text()


def test_project_config_backward_compat_yaml_without_venv_path(tmp_path):
    """v4 yaml without venv_path field loads fine with venv_path=None."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text(
        "provider: claude\nschema_version: 4\nai_hats_dir: .agent/ai-hats\n"
    )

    config = ProjectConfig.from_yaml(path)
    assert config.venv_path is None


def test_project_config_rejects_invalid_venv_path(tmp_path):
    """Invalid venv_path (dotdot escape) raises ProjectConfigError on load."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text(
        "provider: claude\nschema_version: 4\n"
        "ai_hats_dir: .agent/ai-hats\nvenv_path: '../escape'\n"
    )

    with pytest.raises(ProjectConfigError) as exc:
        ProjectConfig.from_yaml(path)
    assert "venv_path" in str(exc.value)


def test_project_config_venv_path_constructor_validates():
    """ProjectConfig() constructor (not from_yaml) also rejects invalid value."""
    with pytest.raises(Exception):  # pydantic ValidationError
        ProjectConfig(provider="claude", venv_path="../bad")


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
    # HATS-252: single LLM call → only review_model remains.
    assert c.review_model is None


def test_session_retro_config_roundtrip():
    c = SessionRetroConfig(
        policy=FeedbackPolicy.HINT,
        smart_threshold=SmartThreshold(min_turns=10, min_tool_calls=5),
        background=False,
        review_model="claude-sonnet-4-6",
    )
    restored = SessionRetroConfig.from_dict(c.to_dict())
    assert restored.review_model == "claude-sonnet-4-6"
    assert restored.policy == FeedbackPolicy.HINT


def test_session_retro_config_loads_legacy_yaml_silently_drops_unknown():
    """Old ai-hats.yaml with removed `mode`/`model` fields must still parse."""
    legacy = {
        "policy": "smart",
        "background": True,
        "mode": "llm",  # silently ignored after HATS-235
        "model": "claude-haiku-4-5",  # silently ignored after HATS-252
    }
    c = SessionRetroConfig.from_dict(legacy)
    assert c.policy == FeedbackPolicy.SMART
    assert c.background is True
    assert c.review_model is None


def test_session_retro_config_reflect_model_alias_emits_warning():
    """Pre-HATS-252 `reflect_model:` is copied into review_model with a warning."""
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        c = SessionRetroConfig.from_dict({"reflect_model": "claude-sonnet-4-6"})
    assert c.review_model == "claude-sonnet-4-6"
    assert any(
        issubclass(w.category, DeprecationWarning) and "reflect_model" in str(w.message)
        for w in caught
    )


def test_session_retro_config_explicit_review_model_wins_over_alias():
    """When both fields are present, review_model wins; no warning fires."""
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        c = SessionRetroConfig.from_dict(
            {
                "review_model": "claude-opus-4-7",
                "reflect_model": "claude-sonnet-4-6",
            }
        )
    assert c.review_model == "claude-opus-4-7"
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_feedback_config_defaults():
    fc = FeedbackConfig()
    assert fc.session_retro.policy == FeedbackPolicy.SMART
    assert fc.is_default


def test_feedback_config_is_default_false_after_change():
    fc = FeedbackConfig()
    fc.session_retro.policy = FeedbackPolicy.OFF
    assert not fc.is_default


def test_feedback_config_roundtrip():
    fc = FeedbackConfig(
        session_retro=SessionRetroConfig(policy=FeedbackPolicy.ALWAYS),
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
            session_retro=SessionRetroConfig(policy=FeedbackPolicy.HINT),
        ),
    )
    path = tmp_path / "ai-hats.yaml"
    config.save(path)

    loaded = ProjectConfig.from_yaml(path)
    assert loaded.active_role == "assistant"
    assert loaded.provider == "claude"
    assert loaded.feedback.session_retro.policy == FeedbackPolicy.HINT
    assert loaded.schema_version == 4


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
    profile_path.write_text(
        json.dumps(
            {
                "active_role": "assistant",
                "provider": "claude",
                "feedback": {
                    "session_retro": {"policy": "hint", "mode": "llm"},
                    "judge": {"policy": "manual"},
                },
            }
        )
    )

    config = ProjectConfig.from_yaml(yaml_path)
    assert config.schema_version == 4
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
    assert config.schema_version == 4
    assert config.provider == "claude"
    assert config.active_role == ""
    assert config.feedback.is_default


def test_migration_idempotent(tmp_path):
    """Loading a v2 config does not re-migrate."""
    config = ProjectConfig(provider="claude", active_role="sre")
    path = tmp_path / "ai-hats.yaml"
    config.save(path)

    loaded = ProjectConfig.from_yaml(path)
    assert loaded.schema_version == 4
    assert loaded.active_role == "sre"


# -- HATS-264: SkillMetadata triggers / skip --


def test_skill_metadata_defaults_have_empty_triggers_and_skip():
    meta = SkillMetadata()
    assert meta.triggers == []
    assert meta.skip == []


def test_skill_metadata_loads_triggers_and_skip(tmp_path):
    path = tmp_path / "metadata.yaml"
    path.write_text(
        "name: foo\n"
        "description: foo skill\n"
        "triggers:\n"
        "  - user asks to debug\n"
        "  - failing test before fix\n"
        "skip:\n"
        "  - trivial typo fix\n"
    )
    meta = SkillMetadata.from_yaml(path)
    assert meta.triggers == ["user asks to debug", "failing test before fix"]
    assert meta.skip == ["trivial typo fix"]


def test_skill_metadata_missing_yaml_returns_empty_lists(tmp_path):
    meta = SkillMetadata.from_yaml(tmp_path / "absent.yaml")
    assert meta.triggers == []
    assert meta.skip == []


# -- HATS-290: imports_order configurability --


def test_imports_order_defaults_to_none():
    config = ProjectConfig()
    assert config.imports_order is None
    # Default omitted from serialization.
    assert "imports_order" not in config.to_dict()


@pytest.mark.parametrize(
    "preset",
    ["default", "role-first", "constraints-first", "anthropic"],
)
def test_imports_order_accepts_known_preset(preset):
    config = ProjectConfig(imports_order=preset)
    assert config.imports_order == preset
    assert config.to_dict()["imports_order"] == preset


def test_imports_order_rejects_unknown_preset():
    with pytest.raises(ValueError, match="unknown imports_order preset"):
        ProjectConfig(imports_order="weird-mode")


def test_imports_order_accepts_full_permutation_list():
    custom = ["role", "rules", "user-rules", "priorities", "traits", "skills_index"]
    config = ProjectConfig(imports_order=custom)
    assert config.imports_order == custom
    assert config.to_dict()["imports_order"] == custom


def test_imports_order_rejects_list_with_unknown_section():
    bad = ["role", "rules", "user-rules", "priorities", "traits", "GHOST"]
    with pytest.raises(ValueError, match="unknown imports_order section"):
        ProjectConfig(imports_order=bad)


def test_imports_order_rejects_list_with_duplicate_section():
    bad = ["role", "role", "user-rules", "priorities", "traits", "skills_index"]
    with pytest.raises(ValueError, match="duplicate imports_order section"):
        ProjectConfig(imports_order=bad)


def test_imports_order_rejects_list_missing_sections():
    bad = ["role", "priorities", "traits", "skills_index"]  # missing rules + user-rules
    with pytest.raises(ValueError, match="missing sections"):
        ProjectConfig(imports_order=bad)


def test_imports_order_rejects_wrong_type():
    # Pydantic's union typing rejects non-str/list/None values before our
    # field_validator runs; either layer surfacing the rejection is fine.
    with pytest.raises(ValueError, match=r"imports_order"):
        ProjectConfig(imports_order=42)


def test_imports_order_round_trip_via_yaml(tmp_path):
    src = tmp_path / "ai-hats.yaml"
    config = ProjectConfig(provider="claude", imports_order="role-first")
    config.save(src)

    loaded = ProjectConfig.from_yaml(src)
    assert loaded.imports_order == "role-first"


def test_imports_order_custom_list_round_trip_via_yaml(tmp_path):
    src = tmp_path / "ai-hats.yaml"
    custom = ["rules", "user-rules", "role", "priorities", "traits", "skills_index"]
    config = ProjectConfig(provider="claude", imports_order=custom)
    config.save(src)

    loaded = ProjectConfig.from_yaml(src)
    assert loaded.imports_order == custom


# ---------- Attachment / TaskCard.attachments (HATS-402) ----------


def test_attachment_digest_validation_accepts_12_hex():
    Attachment(name="plan.md", digest="a1b2c3d4e5f6", added="2026-05-20T00:00:00Z")


@pytest.mark.parametrize(
    "bad",
    [
        "a1b2c3d4e5",  # 10 chars — too short
        "a1b2c3d4e5f60",  # 13 chars — too long
        "A1B2C3D4E5F6",  # uppercase
        "g1b2c3d4e5f6",  # non-hex char
        "a1b2c3d4e5f60000000000000000000000000000000000000000000000000000",  # full sha256 — must reject
    ],
)
def test_attachment_digest_validation_rejects_invalid(bad):
    with pytest.raises(Exception):
        Attachment(name="x", digest=bad, added="2026-05-20T00:00:00Z")


def test_attachment_digest_empty_allowed_for_construction():
    """Default-constructed Attachment (e.g. before populating) tolerates empty digest."""
    Attachment()


def test_task_card_attachments_field_round_trip():
    card = TaskCard(
        id="T-1",
        title="Test",
        attachments=[
            Attachment(
                name="plan.md",
                digest="a1b2c3d4e5f6",
                added="2026-05-20T00:00:00Z",
                note="design",
            )
        ],
    )
    out = card.to_dict()
    assert out["attachments"] == [
        {
            "name": "plan.md",
            "digest": "a1b2c3d4e5f6",
            "added": "2026-05-20T00:00:00Z",
            "note": "design",
        }
    ]
    reloaded = TaskCard.from_dict(out)
    assert reloaded.attachments[0].name == "plan.md"
    assert reloaded.attachments[0].digest == "a1b2c3d4e5f6"


def test_task_card_empty_attachments_omitted_from_output():
    card = TaskCard.from_dict({"id": "T-1", "title": "Test"})
    out = card.to_dict()
    assert "attachments" not in out


def test_task_card_legacy_yaml_without_attachments_loads_as_empty_list():
    """Existing YAML predating HATS-402 must load without errors and yield []."""
    card = TaskCard.from_dict({"id": "T-1", "title": "Test"})
    assert card.attachments == []


def test_task_card_attachments_not_captured_into_extras():
    """attachments is a typed field — must not leak into extras."""
    data = {
        "id": "T-1",
        "title": "Test",
        "attachments": [
            {"name": "x.md", "digest": "0123456789ab", "added": "", "note": ""}
        ],
    }
    card = TaskCard.from_dict(data)
    assert len(card.attachments) == 1
    assert "attachments" not in card.extras
