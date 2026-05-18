"""Tests for pipeline.loader — YAML schema + registry resolution."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.pipeline.loader import PipelineYamlError, load_pipeline


_BUILTIN_DIR = (
    Path(__file__).parent.parent / "library/core/pipelines"
)


@pytest.mark.parametrize(
    "name", ["human", "execute", "reflect-all", "reflect-session"]
)
def test_load_each_builtin(name: str):
    p = load_pipeline(_BUILTIN_DIR / f"{name}.yaml")
    assert p.io.name == name
    assert len(p.steps) >= 1


def test_load_invalid_yaml(tmp_path: Path):
    f = tmp_path / "bad.yaml"
    f.write_text("name: x\nsteps: [: : :")
    with pytest.raises(PipelineYamlError, match="invalid YAML"):
        load_pipeline(f)


def test_load_unknown_step(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text("name: x\nsteps:\n  - id: nonexistent_step\n")
    with pytest.raises(PipelineYamlError, match="unknown step"):
        load_pipeline(f)


def test_load_missing_top_level_name(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text("steps:\n  - id: pre_log\n")
    with pytest.raises(PipelineYamlError, match="'name' must be"):
        load_pipeline(f)


def test_load_empty_steps(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text("name: x\nsteps: []\n")
    with pytest.raises(PipelineYamlError, match="non-empty list"):
        load_pipeline(f)


def test_load_step_missing_id(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text("name: x\nsteps:\n  - params: {}\n")
    with pytest.raises(PipelineYamlError, match="id must be"):
        load_pipeline(f)


def test_load_invalid_step_params(tmp_path: Path):
    f = tmp_path / "p.yaml"
    # extract_marker requires start/end/out_key
    f.write_text(
        "name: x\nsteps:\n"
        "  - id: extract_marker\n"
        "    params: {start: A}\n"
    )
    with pytest.raises(PipelineYamlError, match="missing param"):
        load_pipeline(f)


def test_load_top_level_not_mapping(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text("- just\n- a\n- list\n")
    with pytest.raises(PipelineYamlError, match="top-level"):
        load_pipeline(f)


# ---- HATS-378 Phase 0: harness policy on pipeline steps ----


def test_load_step_without_harness_block_has_none_policy(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text(
        "name: x\nsteps:\n"
        "  - id: pre_log\n"
    )
    p = load_pipeline(f)
    assert p.steps[0].harness_policy is None


def test_load_step_with_harness_block_attaches_policy(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text(
        "name: x\nsteps:\n"
        "  - id: pre_log\n"
        "    harness:\n"
        "      reporting: true\n"
        "      on_zero_output: harness_incident\n"
        "      on_timeout:\n"
        "        retry: 1\n"
        "        budget_multiplier: 2\n"
        "        then: harness_incident\n"
    )
    p = load_pipeline(f)
    policy = p.steps[0].harness_policy
    assert policy is not None
    assert policy.reporting is True
    assert policy.on_zero_output == "harness_incident"
    assert policy.on_timeout is not None
    assert policy.on_timeout.retry == 1
    assert policy.on_timeout.budget_multiplier == 2.0


def test_load_step_with_invalid_harness_block_raises(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text(
        "name: x\nsteps:\n"
        "  - id: pre_log\n"
        "    harness:\n"
        "      reporting: maybe\n"
    )
    with pytest.raises(PipelineYamlError, match="harness:.*reporting must be bool"):
        load_pipeline(f)


# ---- __main__ dry-run inspector ----


@pytest.mark.parametrize(
    "name", ["human", "execute", "reflect-all", "reflect-session"]
)
def test_loader_main_inspects_each_builtin(name: str):
    proc = subprocess.run(
        [sys.executable, "-m", "ai_hats.pipeline.loader",
         str(_BUILTIN_DIR / f"{name}.yaml")],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"Pipeline: {name}" in proc.stdout
    assert "Steps:" in proc.stdout
    assert "external requires" in proc.stdout
