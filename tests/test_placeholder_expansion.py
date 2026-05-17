"""Tests for `<ai_hats_dir>` placeholder expansion (HATS-380)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.placeholders import expand_path_placeholders


def test_default_substitutes_to_relative_agent_path(tmp_path: Path) -> None:
    text = "Write report to <ai_hats_dir>/sessions/retros/foo.md"
    out = expand_path_placeholders(text, tmp_path)
    assert "<ai_hats_dir>" not in out
    assert ".agent/ai-hats/sessions/retros/foo.md" in out


def test_no_op_when_placeholder_absent(tmp_path: Path) -> None:
    text = "plain text without placeholder"
    assert expand_path_placeholders(text, tmp_path) == text


def test_idempotent(tmp_path: Path) -> None:
    text = "<ai_hats_dir>/x"
    once = expand_path_placeholders(text, tmp_path)
    twice = expand_path_placeholders(once, tmp_path)
    assert once == twice


def test_respects_custom_ai_hats_dir_yaml(tmp_path: Path) -> None:
    # Custom ai_hats_dir via ai-hats.yaml.
    (tmp_path / "ai-hats.yaml").write_text("ai_hats_dir: custom/hats\n")
    out = expand_path_placeholders("path: <ai_hats_dir>/state", tmp_path)
    assert "custom/hats/state" in out
    assert "<ai_hats_dir>" not in out


def test_respects_env_override_inside_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "env-hats"
    monkeypatch.setenv("AI_HATS_DIR", str(target))
    out = expand_path_placeholders("<ai_hats_dir>/foo", tmp_path)
    assert "<ai_hats_dir>" not in out
    # Absolute env path is outside project_dir relativization → falls back to
    # absolute POSIX path. Either way, the literal placeholder must be gone.
    assert "env-hats/foo" in out


def test_env_outside_project_falls_back_to_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "elsewhere"
    monkeypatch.setenv("AI_HATS_DIR", str(outside))
    out = expand_path_placeholders("<ai_hats_dir>/x", project)
    assert "<ai_hats_dir>" not in out
    assert str(outside).replace("\\", "/") + "/x" in out


def test_multiple_occurrences(tmp_path: Path) -> None:
    text = "<ai_hats_dir>/a and <ai_hats_dir>/b"
    out = expand_path_placeholders(text, tmp_path)
    assert out.count(".agent/ai-hats") == 2
    assert "<ai_hats_dir>" not in out
