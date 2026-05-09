"""Tests for ai-hats path conventions (HATS-274, HATS-275)."""

from __future__ import annotations


from ai_hats.paths import ai_hats_dir, pipeline_steps_dir, traces_dir


def test_ai_hats_dir_default(tmp_path, monkeypatch):
    """No env override → default <project>/.agent/ai-hats/."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    base = ai_hats_dir(tmp_path)
    assert base == tmp_path / ".agent" / "ai-hats"
    assert base.is_dir()


def test_ai_hats_dir_env_override(tmp_path, monkeypatch):
    """AI_HATS_DIR overrides default. Project dir ignored."""
    custom = tmp_path / "custom-runtime"
    monkeypatch.setenv("AI_HATS_DIR", str(custom))
    base = ai_hats_dir(tmp_path / "project")
    assert base == custom
    assert base.is_dir()


def test_ai_hats_dir_env_expands_user(tmp_path, monkeypatch):
    """AI_HATS_DIR with ~ gets expanded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AI_HATS_DIR", "~/my-ai-hats")
    base = ai_hats_dir(tmp_path / "project")
    assert base == tmp_path / "my-ai-hats"
    assert base.is_dir()


def test_ai_hats_dir_idempotent_mkdir(tmp_path, monkeypatch):
    """Calling twice doesn't fail and returns same path."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    first = ai_hats_dir(tmp_path)
    second = ai_hats_dir(tmp_path)
    assert first == second
    assert first.is_dir()


def test_traces_dir_under_ai_hats(tmp_path, monkeypatch):
    """traces_dir is <ai_hats_dir>/traces and gets created."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    td = traces_dir(tmp_path)
    assert td == tmp_path / ".agent" / "ai-hats" / "traces"
    assert td.is_dir()


def test_traces_dir_respects_env_override(tmp_path, monkeypatch):
    """AI_HATS_DIR cascades: traces_dir lives under the override."""
    custom = tmp_path / "custom"
    monkeypatch.setenv("AI_HATS_DIR", str(custom))
    td = traces_dir(tmp_path / "project")
    assert td == custom / "traces"
    assert td.is_dir()


def test_pipeline_steps_dir_under_ai_hats(tmp_path, monkeypatch):
    """pipeline_steps_dir is <ai_hats_dir>/pipeline_steps/."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    psd = pipeline_steps_dir(tmp_path)
    assert psd == tmp_path / ".agent" / "ai-hats" / "pipeline_steps"
    assert psd.is_dir()


def test_pipeline_steps_dir_respects_env_override(tmp_path, monkeypatch):
    """AI_HATS_DIR cascades into pipeline_steps_dir resolution too."""
    custom = tmp_path / "custom"
    monkeypatch.setenv("AI_HATS_DIR", str(custom))
    psd = pipeline_steps_dir(tmp_path / "project")
    assert psd == custom / "pipeline_steps"
    assert psd.is_dir()
