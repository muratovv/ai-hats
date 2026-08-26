"""HATS-833 — session-start drift net: per-surface change detectors + sync_hooks
orchestration across ALL managed-hook surfaces (runtime bytes + wiring, wt bytes,
git). Detection is drift-gated and reports WHAT changed (name + kind) so the
session-start heal note can name it.
"""

import json
import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.constants import HOOK_POST_TOOL_USE
from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.surfaces.claude.provider import ClaudeSurface


# ----- fixtures / helpers -----


@pytest.fixture
def assembler(tmp_path: Path) -> Assembler:
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    return Assembler(project_dir=project)


def _result(skills: list[ResolvedComponent]) -> CompositionResult:
    return CompositionResult(name="r", priorities=[], rules=[], skills=skills, injections=[])


def _skill_runtime(base: Path, name: str, event: str, matcher: str, script: str):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\nai_hats:\n  runtime_hooks:\n    {event}:\n"
        f"      - matcher: {matcher}\n        script: {script}\n---\n# {name}\n"
    )
    sp = d / script
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("#!/usr/bin/env bash\nexit 0\n")
    sp.chmod(0o755)
    return ResolvedComponent(name=name, component_type=ComponentKind.SKILL, source_path=d)


# ----- runtime-hook BYTES drift -----


# ----- runtime-hook WIRING drift (retired with root .claude/settings.json) -----


class TestRuntimeWiringRetired:
    """HATS-1170 moved managed hooks to ``<cache>/settings.json`` (ADR-0018), so
    root wiring is neither written nor tracked and there is nothing to drift
    from. Entry *content* is covered in ``tests/test_provider_pretool_hook.py``
    against the cache file; these pin the root staying out of it (HATS-1201).
    """

    def _claude_project(self, tmp_path: Path) -> Assembler:
        project = tmp_path / "proj"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        ProjectConfig(provider="claude").save(project / PROJECT_CONFIG)
        return Assembler(project_dir=project)

    def test_ensure_writes_no_root_settings(self, tmp_path):
        asm = self._claude_project(tmp_path)
        s = _skill_runtime(tmp_path / "sk", "mf", HOOK_POST_TOOL_USE, "Write", "h/f.sh")
        res = _result([s])
        prov = ClaudeSurface()

        prov.ensure_runtime_hooks(asm.project_dir, res)

        assert not (asm.project_dir / ".claude" / "settings.json").exists()
        assert prov.runtime_wiring_changes(asm.project_dir, res) == []

    def test_user_owned_root_settings_are_not_tracked(self, tmp_path):
        """A user's own root settings.json is untouched and never reported as
        drift — ai-hats has no claim on the file any more."""
        asm = self._claude_project(tmp_path)
        s = _skill_runtime(tmp_path / "sk", "mf", HOOK_POST_TOOL_USE, "Write", "h/f.sh")
        res = _result([s])
        sp = asm.project_dir / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        user_settings = json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}})
        sp.write_text(user_settings)

        prov = ClaudeSurface()
        prov.ensure_runtime_hooks(asm.project_dir, res)

        assert sp.read_text() == user_settings
        assert prov.runtime_wiring_changes(asm.project_dir, res) == []


# ----- sync_hooks orchestration (drift-gate / version-skew / per-surface heal) -----
