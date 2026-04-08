"""Tests for retro loader/writer — round-trip and edge cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.retro.bundle import SCHEMA_VERSION as BUNDLE_VERSION
from ai_hats.retro.bundle import BundleV1
from ai_hats.retro.judge_retro import SCHEMA_VERSION as JUDGE_RETRO_VERSION
from ai_hats.retro.judge_retro import JudgeRetroV1
from ai_hats.retro.loader import load, parse
from ai_hats.retro.session_retro import SCHEMA_VERSION as SESSION_RETRO_VERSION
from ai_hats.retro.session_retro import SessionRetroV1
from ai_hats.retro.writer import dump


# --- helpers ---


def _make_session_retro() -> SessionRetroV1:
    return SessionRetroV1.model_validate({
        "schema": SESSION_RETRO_VERSION,
        "session_id": "session_test",
        "project": "test",
        "role": "go-dev",
        "date": "2026-04-08",
        "metrics": {"exit_code": 0, "turns": 5, "tool_calls": 12},
        "summary": "Test session",
        "links": {"audit": "a.md"},
    })


def _make_bundle() -> BundleV1:
    return BundleV1.model_validate({
        "schema": BUNDLE_VERSION,
        "bundle_id": "BUNDLE-2026-04-08-001",
        "project": "test",
        "created": "2026-04-08T09:00:00Z",
        "session_ids": ["session_test"],
    })


def _make_judge_retro() -> JudgeRetroV1:
    return JudgeRetroV1.model_validate({
        "schema": JUDGE_RETRO_VERSION,
        "judge_run_id": "judge-001",
        "project": "test",
        "date": "2026-04-08",
        "bundle_id": "BUNDLE-2026-04-08-001",
        "findings": [{
            "id": "F1",
            "title": "x",
            "category": "process",
            "severity": "low",
            "root_cause": "rc",
            "evidence": [{
                "session_id": "session_test",
                "source": "audit",
                "location": "l",
            }],
        }],
    })


# --- parse() ---


def test_parse_frontmatter_with_body() -> None:
    text = "---\nschema: x\nfoo: bar\n---\n\n# Title\n\nbody\n"
    raw, body = parse(text)
    assert raw == {"schema": "x", "foo": "bar"}
    assert "# Title" in body


def test_parse_pure_yaml() -> None:
    text = "schema: hats-bundle/v1\nbundle_id: BUNDLE-2026-04-08-001\n"
    raw, body = parse(text)
    assert raw["bundle_id"] == "BUNDLE-2026-04-08-001"
    assert body == ""


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


def test_session_retro_round_trip(tmp_path: Path) -> None:
    sr = _make_session_retro()
    p = tmp_path / "s.md"
    dump(sr, p, body="# Test\nbody\n")
    loaded, body = load(p)
    assert isinstance(loaded, SessionRetroV1)
    assert loaded.session_id == sr.session_id
    assert "# Test" in body


def test_bundle_round_trip(tmp_path: Path) -> None:
    b = _make_bundle()
    p = tmp_path / "b.yaml"
    dump(b, p)
    loaded, body = load(p)
    assert isinstance(loaded, BundleV1)
    assert loaded.bundle_id == b.bundle_id
    assert body == ""


def test_judge_retro_round_trip(tmp_path: Path) -> None:
    jr = _make_judge_retro()
    p = tmp_path / "j.md"
    dump(jr, p, body="# Verdict\n")
    loaded, body = load(p)
    assert isinstance(loaded, JudgeRetroV1)
    assert loaded.judge_run_id == jr.judge_run_id
    assert "Verdict" in body


def test_bundle_rejects_body(tmp_path: Path) -> None:
    b = _make_bundle()
    with pytest.raises(ValueError, match="markdown body"):
        dump(b, tmp_path / "b.yaml", body="nope")


# --- dispatch by family ---


def test_load_session_retro_dispatches_correct_class(tmp_path: Path) -> None:
    p = tmp_path / "s.md"
    dump(_make_session_retro(), p)
    loaded, _ = load(p)
    assert type(loaded).__name__ == "SessionRetroV1"


def test_load_bundle_dispatches_correct_class(tmp_path: Path) -> None:
    p = tmp_path / "b.yaml"
    dump(_make_bundle(), p)
    loaded, _ = load(p)
    assert type(loaded).__name__ == "BundleV1"


def test_load_judge_retro_dispatches_correct_class(tmp_path: Path) -> None:
    p = tmp_path / "j.md"
    dump(_make_judge_retro(), p)
    loaded, _ = load(p)
    assert type(loaded).__name__ == "JudgeRetroV1"


def test_load_unknown_family_raises(tmp_path: Path) -> None:
    p = tmp_path / "x.yaml"
    p.write_text("schema: hats-unknown/v1\n")
    with pytest.raises(ValueError, match="Unknown schema family"):
        load(p)


def test_load_missing_schema_field_raises(tmp_path: Path) -> None:
    p = tmp_path / "x.yaml"
    p.write_text("foo: bar\n")
    with pytest.raises(ValueError, match="schema"):
        load(p)
