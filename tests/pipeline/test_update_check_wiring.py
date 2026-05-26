"""Wiring tests for the update-check steps in the production pipelines."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.pipeline import registry
from ai_hats.pipeline.loader import load_pipeline
from ai_hats.pipeline.presets import execute_pipeline


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINES_DIR = REPO_ROOT / "library" / "core" / "pipelines"


def test_check_update_async_registered():
    assert "check_update_async" in registry.names()


def test_render_update_banner_registered():
    assert "render_update_banner" in registry.names()


def test_execute_yaml_starts_with_check_update_async():
    pipeline = load_pipeline(PIPELINES_DIR / "execute.yaml")
    step_names = [s.io.name for s in pipeline.steps]
    assert step_names[0] == "check_update_async", step_names


def test_execute_yaml_ends_with_render_update_banner():
    pipeline = load_pipeline(PIPELINES_DIR / "execute.yaml")
    step_names = [s.io.name for s in pipeline.steps]
    assert step_names[-1] == "render_update_banner", step_names


def test_execute_yaml_check_before_launch():
    # HATS-535: ``launch_provider`` step renamed to ``provider``.
    pipeline = load_pipeline(PIPELINES_DIR / "execute.yaml")
    step_names = [s.io.name for s in pipeline.steps]
    assert step_names.index("check_update_async") < step_names.index("provider")
    assert step_names.index("provider") < step_names.index("render_update_banner")


def test_human_yaml_starts_with_check_update_async():
    pipeline = load_pipeline(PIPELINES_DIR / "human.yaml")
    step_names = [s.io.name for s in pipeline.steps]
    assert step_names[0] == "check_update_async", step_names


def test_human_yaml_ends_with_render_update_banner():
    pipeline = load_pipeline(PIPELINES_DIR / "human.yaml")
    step_names = [s.io.name for s in pipeline.steps]
    assert step_names[-1] == "render_update_banner", step_names


def test_presets_execute_pipeline_has_both_steps():
    step_names = [s.io.name for s in execute_pipeline.steps]
    assert step_names[0] == "check_update_async"
    assert step_names[-1] == "render_update_banner"


@pytest.mark.parametrize("yaml_name", ["reflect-session.yaml", "reflect-all.yaml",
                                       "reflect-role.yaml", "reflect-issue.yaml"])
def test_reflect_pipelines_unchanged(yaml_name):
    """Sub-pipelines must NOT carry update-check steps — they are not main sessions."""
    pipeline = load_pipeline(PIPELINES_DIR / yaml_name)
    step_names = [s.io.name for s in pipeline.steps]
    assert "check_update_async" not in step_names, step_names
    assert "render_update_banner" not in step_names, step_names
