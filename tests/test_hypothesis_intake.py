"""Unit tests for the `reflect-issue` intake YAML parser."""

from __future__ import annotations

import pytest

from ai_hats.hypothesis.intake import (
    CreateAction,
    IntakeParseError,
    MergeAction,
    parse_intake_yaml,
)


def test_parses_create_action_with_all_draft_fields():
    text = """
    action: create
    draft:
      title: agent skips parameterized queries
      hypothesis: agent reaches for string concatenation in SQL paths
      baseline: every SQL call we audited used f-strings
      expected_outcome:
        - audit catches 0 string-concat SQL calls
        - lint rule blocks new ones
      success_criterion: zero string-concat SQL in 4 consecutive sessions
      exit_criteria:
        confirm:
          - 4 sessions with zero string-concat SQL
        refute:
          - any session shows recurring concat after rule landed
        stalled:
          - no SQL paths touched for 3 weeks
    """
    result = parse_intake_yaml(text)
    assert isinstance(result, CreateAction)
    assert result.action == "create"
    assert result.draft.title.startswith("agent skips")
    assert result.draft.exit_criteria is not None
    assert "confirm" in result.draft.exit_criteria
    assert len(result.draft.expected_outcome) == 2


def test_parses_create_minimal_fields():
    text = """
    action: create
    draft:
      title: short
      hypothesis: agent does X
    """
    result = parse_intake_yaml(text)
    assert isinstance(result, CreateAction)
    assert result.draft.baseline is None
    assert result.draft.expected_outcome == []


def test_parses_merge_action():
    text = """
    action: merge
    target_id: HYP-007
    evidence: observed once more in this session while reviewing pipeline.py
    """
    result = parse_intake_yaml(text)
    assert isinstance(result, MergeAction)
    assert result.target_id == "HYP-007"
    assert result.evidence.startswith("observed once more")


def test_empty_text_raises():
    with pytest.raises(IntakeParseError, match="empty"):
        parse_intake_yaml("")


def test_invalid_yaml_raises():
    with pytest.raises(IntakeParseError, match="invalid YAML"):
        parse_intake_yaml("action: create\n  bad: indent")


def test_non_mapping_raises():
    with pytest.raises(IntakeParseError, match="expected YAML mapping"):
        parse_intake_yaml("- just\n- a\n- list")


def test_unknown_action_raises():
    with pytest.raises(IntakeParseError, match="unknown action"):
        parse_intake_yaml("action: refute\ntarget_id: HYP-001")


def test_merge_with_bad_target_id_raises():
    with pytest.raises(IntakeParseError, match="schema mismatch"):
        parse_intake_yaml("action: merge\ntarget_id: foo\nevidence: x")


def test_merge_missing_evidence_raises():
    with pytest.raises(IntakeParseError, match="schema mismatch"):
        parse_intake_yaml("action: merge\ntarget_id: HYP-001\nevidence: ''")


def test_create_missing_title_raises():
    with pytest.raises(IntakeParseError, match="schema mismatch"):
        parse_intake_yaml(
            "action: create\ndraft:\n  title: ''\n  hypothesis: x"
        )


def test_create_action_rejects_extra_top_level_key():
    with pytest.raises(IntakeParseError, match="schema mismatch"):
        parse_intake_yaml(
            "action: create\ndraft:\n  title: t\n  hypothesis: h\nextra: 1"
        )
