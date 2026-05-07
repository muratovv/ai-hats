"""Tests for retro loader/writer — round-trip and edge cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.retro.loader import load, parse
from ai_hats.retro.session_review_schema import SCHEMA_VERSION as REVIEW_VERSION
from ai_hats.retro.session_review_schema import SessionReviewV1
from ai_hats.retro.writer import dump


# --- helpers ---


def _make_session_review() -> SessionReviewV1:
    return SessionReviewV1.model_validate({
        "schema": REVIEW_VERSION,
        "session_id": "session_test",
        "project": "test",
        "role": "go-dev",
        "date": "2026-04-08",
        "timestamp": "2026-04-08T12:00:00Z",
        "metrics": {"exit_code": 0, "turns": 5, "tool_calls": 12},
        "links": {"audit": "a.md"},
        "summary": "Test session",
        "observations": [],
        "hypothesis_verdicts": [],
        "proposal_actions": [],
        "self_problems": [],
    })


# --- parse() ---


def test_parse_frontmatter_with_body() -> None:
    text = "---\nschema: x\nfoo: bar\n---\n\n# Title\n\nbody\n"
    raw, body = parse(text)
    assert raw == {"schema": "x", "foo": "bar"}
    assert "# Title" in body


def test_parse_rejects_missing_closing_marker() -> None:
    with pytest.raises(ValueError, match="closing"):
        parse("---\nschema: x\nfoo: bar\n")


def test_parse_rejects_empty() -> None:
    with pytest.raises(ValueError):
        parse("")


def test_parse_rejects_non_mapping_yaml() -> None:
    with pytest.raises(ValueError, match="mapping"):
        parse("- item1\n- item2\n")


def test_parse_rejects_non_mapping_frontmatter() -> None:
    with pytest.raises(ValueError, match="mapping"):
        parse("---\n- item1\n- item2\n---\n\nbody\n")


# --- round-trip ---


def test_session_review_round_trip(tmp_path: Path) -> None:
    sr = _make_session_review()
    p = tmp_path / "s.md"
    dump(sr, p, body="# Test\nbody\n")
    loaded, body = load(p)
    assert isinstance(loaded, SessionReviewV1)
    assert loaded.session_id == sr.session_id
    assert "# Test" in body


# --- dispatch by family ---


def test_load_session_review_dispatches_correct_class(tmp_path: Path) -> None:
    p = tmp_path / "s.md"
    dump(_make_session_review(), p)
    loaded, _ = load(p)
    assert type(loaded).__name__ == "SessionReviewV1"


def test_load_unknown_family_raises(tmp_path: Path) -> None:
    p = tmp_path / "x.yaml"
    p.write_text("---\nschema: hats-unknown/v1\n---\n\n")
    with pytest.raises(ValueError, match="Unknown schema family"):
        load(p)


def test_load_missing_schema_field_raises(tmp_path: Path) -> None:
    p = tmp_path / "x.yaml"
    p.write_text("---\nfoo: bar\n---\n\n")
    with pytest.raises(ValueError, match="schema"):
        load(p)
