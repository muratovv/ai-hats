"""Characterization goldens per (surface, run_mode) pair (HATS-1207 S1).

Pins the baseline dry-run payload for each of the 6 (surface ∈ {claude, agy, cline})
× (run_mode ∈ {hitl, automate}) pairs under default SessionPolicy().

Recorded against master behavior before production builder refactor.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from ai_hats.assembler import Assembler
from ai_hats.dry_run import dry_run_automate, dry_run_hitl
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import SessionPolicy

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


@pytest.fixture
def project_factory(tmp_path: Path):
    """Fixture to build a temp project configured for a given provider."""
    def _create(provider_name: str) -> Path:
        project = tmp_path / f"proj_{provider_name}"
        project.mkdir(parents=True, exist_ok=True)
        ProjectConfig(
            provider=provider_name,
            library_paths=[str(LIBRARY_DIR)],
            ai_hats_dir=".agent/ai-hats",
            active_role="maintainer",
            default_role="maintainer",
        ).save(project / PROJECT_CONFIG)
        asm = Assembler(project, library_paths=[LIBRARY_DIR])
        asm.init()
        asm.set_role("maintainer", provider_name=provider_name)
        return project
    return _create


@pytest.mark.parametrize("surface", ["claude", "agy", "cline"])
def test_golden_hitl_default_policy(project_factory, surface: str):
    """Pin HITL dry-run output under default policy."""
    project = project_factory(surface)
    report = dry_run_hitl(project, role="maintainer", provider=surface, policy=SessionPolicy())
    d = report.to_dict()

    assert d["role"] == "maintainer"
    assert d["provider"] == surface
    assert d["run_mode"] == "hitl"
    assert d["policy"] == {"context": True, "hooks": True, "settings": True}
    assert isinstance(d["launch"], list)
    assert len(d["launch"]) > 0
    assert isinstance(d["env_keys"], list)
    assert isinstance(d["materialized"], list)

    # Surface-specific baseline checks
    if surface == "claude":
        assert any("--system-prompt-file" in arg for arg in d["launch"])
    elif surface == "agy":
        assert any("--add-dir" in arg for arg in d["launch"])
    elif surface == "cline":
        assert any("-i" == arg for arg in d["launch"])


@pytest.mark.parametrize("surface", ["claude", "agy", "cline"])
def test_golden_automate_default_policy(project_factory, surface: str):
    """Pin AUTOMATE dry-run output under default policy."""
    project = project_factory(surface)
    report = dry_run_automate(
        project, role="maintainer", provider=surface, task="test task", policy=SessionPolicy()
    )
    d = report.to_dict()

    assert d["role"] == "maintainer"
    assert d["provider"] == surface
    assert d["run_mode"] == "automate"
    assert d["policy"] == {"context": True, "hooks": True, "settings": True}
    assert isinstance(d["launch"], list)
    assert len(d["launch"]) > 0
    assert isinstance(d["notes"], list)

@pytest.mark.parametrize("surface", ["claude", "agy", "cline"])
def test_policy_context_false_hitl(project_factory, surface: str):
    """policy=SessionPolicy(context=False) suppresses CONTEXT for HITL mode across all surfaces."""
    project = project_factory(surface)
    report = dry_run_hitl(project, role="maintainer", provider=surface, policy=SessionPolicy(context=False))
    d = report.to_dict()

    if surface == "claude":
        assert not any("--system-prompt-file" in arg for arg in d["launch"])
    elif surface == "agy":
        assert not any("--add-dir" in arg for arg in d["launch"])
    elif surface == "cline":
        assert not any("-s" in arg for arg in d["launch"])
        # M5 regression check: cline HITL retains -i even when CONTEXT is suppressed
        assert any("-i" == arg for arg in d["launch"])


@pytest.mark.parametrize("surface", ["claude", "agy", "cline"])
def test_policy_context_false_automate(project_factory, surface: str):
    """policy=SessionPolicy(context=False) suppresses CONTEXT for AUTOMATE mode across all surfaces."""
    project = project_factory(surface)
    report = dry_run_automate(
        project, role="maintainer", provider=surface, task="test task", policy=SessionPolicy(context=False)
    )
    d = report.to_dict()

    if surface == "claude":
        sys_arg = next((arg for arg in d["launch"] if arg.startswith("system_prompt=")), None)
        assert sys_arg is None or "system_prompt=None" in sys_arg or "'append': ''" in sys_arg or "system_prompt={}" in sys_arg
    elif surface in ("agy", "cline"):
        # meta-prompt does not contain # SYSTEM_ROLE when context=False
        launch_str = " ".join(d["launch"])
        assert "# SYSTEM_ROLE" not in launch_str
