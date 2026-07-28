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
from ai_hats.constants import HOOK_PRE_TOOL_USE, HOOK_POST_TOOL_USE
from ai_hats.hooks_manager import HookChange
from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.models import ProjectConfig
from ai_hats.paths import (
    hooks_dir,
    managed_runtime_hook_filename,
    managed_wt_hook_filename,
    wt_hooks_dir,
    PROJECT_CONFIG,
)
from ai_hats.surfaces.claude.provider import ClaudeProvider


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


def _skill_wt(base: Path, name: str, script: str):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\nai_hats:\n  worktree:\n    wt_out:\n"
        f"      - script: {script}\n        on: [merge]\n---\n# {name}\n"
    )
    sp = d / script
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("#!/usr/bin/env bash\nexit 0\n")
    sp.chmod(0o755)
    return ResolvedComponent(name=name, component_type=ComponentKind.SKILL, source_path=d)


# ----- runtime-hook BYTES drift -----


class TestRuntimeBytesDrift:
    def test_in_sync_after_materialize(self, assembler, tmp_path):
        s = _skill_runtime(tmp_path / "sk", "sa", HOOK_PRE_TOOL_USE, "Bash", "h/a.sh")
        res = _result([s])
        assembler.hooks.materialize_runtime_hooks(res)
        assert assembler.hooks._runtime_bytes_changes(res) == []

    def test_missing_script_reported(self, assembler, tmp_path):
        s = _skill_runtime(tmp_path / "sk", "sa", HOOK_PRE_TOOL_USE, "Bash", "h/a.sh")
        res = _result([s])
        assembler.hooks.materialize_runtime_hooks(res)
        dest = hooks_dir(assembler.project_dir) / managed_runtime_hook_filename("sa", "h/a.sh")
        dest.unlink()
        changes = assembler.hooks._runtime_bytes_changes(res)
        assert (dest.name, "missing") in changes

    def test_content_drift_reported(self, assembler, tmp_path):
        s = _skill_runtime(tmp_path / "sk", "sa", HOOK_PRE_TOOL_USE, "Bash", "h/a.sh")
        res = _result([s])
        assembler.hooks.materialize_runtime_hooks(res)
        dest = hooks_dir(assembler.project_dir) / managed_runtime_hook_filename("sa", "h/a.sh")
        dest.write_text("#!/usr/bin/env bash\necho drifted\n")
        changes = assembler.hooks._runtime_bytes_changes(res)
        assert (dest.name, "content") in changes

    def test_stale_reported_when_skill_leaves(self, assembler, tmp_path):
        s = _skill_runtime(tmp_path / "sk", "sa", HOOK_PRE_TOOL_USE, "Bash", "h/a.sh")
        assembler.hooks.materialize_runtime_hooks(_result([s]))
        # Skill gone from composition, file+manifest still list it → stale.
        name = managed_runtime_hook_filename("sa", "h/a.sh")
        changes = assembler.hooks._runtime_bytes_changes(_result([]))
        assert (name, "stale") in changes

    def test_package_guard_helper_tracked(self, assembler, tmp_path):
        # The classifier helper is materialized but NOT wired — the bytes
        # detector must still cover it (review pt-2). Delete it → missing.
        res = _result([])
        assembler.hooks.materialize_runtime_hooks(res)
        helper = hooks_dir(assembler.project_dir) / "shared_state_classifier.sh"
        if helper.exists():  # only if the package ships it
            helper.unlink()
            assert (
                "shared_state_classifier.sh",
                "missing",
            ) in assembler.hooks._runtime_bytes_changes(res)


# ----- wt-hook BYTES drift -----


class TestWtBytesDrift:
    def test_in_sync_after_materialize(self, assembler, tmp_path):
        s = _skill_wt(tmp_path / "sk", "drn", "d.sh")
        res = _result([s])
        assembler.hooks.materialize_worktree_hooks(res)
        assert assembler.hooks._wt_hooks_changes(res) == []

    def test_missing_reported(self, assembler, tmp_path):
        s = _skill_wt(tmp_path / "sk", "drn", "d.sh")
        res = _result([s])
        assembler.hooks.materialize_worktree_hooks(res)
        dest = wt_hooks_dir(assembler.project_dir) / managed_wt_hook_filename("drn", "d.sh")
        dest.unlink()
        changes = assembler.hooks._wt_hooks_changes(res)
        assert HookChange("wt", dest.name, "missing") in changes

    def test_clean_project_no_changes(self, assembler):
        assert assembler.hooks._wt_hooks_changes(_result([])) == []


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
        prov = ClaudeProvider()

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

        prov = ClaudeProvider()
        prov.ensure_runtime_hooks(asm.project_dir, res)

        assert sp.read_text() == user_settings
        assert prov.runtime_wiring_changes(asm.project_dir, res) == []


# ----- sync_hooks orchestration (drift-gate / version-skew / per-surface heal) -----


class TestSyncHooksOrchestration:
    def _role_project(self, tmp_path: Path) -> Assembler:
        project = tmp_path / "proj"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        ProjectConfig(provider="claude", active_role="r", default_role="r").save(
            project / PROJECT_CONFIG
        )
        return Assembler(project_dir=project)

    def _wire(self, monkeypatch, asm, *, runtime, wt, git, behind=False):
        calls: list[str] = []
        monkeypatch.setattr(asm.hooks, "_runtime_hooks_changes", lambda result, provider: runtime)
        monkeypatch.setattr(asm.hooks, "_wt_hooks_changes", lambda result: wt)
        monkeypatch.setattr(asm.hooks, "_git_hooks_changes", lambda result: git)
        monkeypatch.setattr(asm.hooks, "binary_behind_source", lambda: behind)
        monkeypatch.setattr(
            asm.hooks, "materialize_runtime_hooks", lambda result: calls.append("rt_bytes")
        )
        monkeypatch.setattr(
            asm.hooks, "materialize_worktree_hooks", lambda result: calls.append("wt")
        )
        monkeypatch.setattr(asm.hooks, "install_git_hooks", lambda result: calls.append("git"))
        monkeypatch.setattr(
            # HATS-1130: ec85f43d relocated ClaudeProvider into surfaces/.
            "ai_hats.surfaces.claude.provider.ClaudeProvider.ensure_runtime_hooks",
            lambda self, p, r: calls.append("rt_wire"),
        )
        return calls

    def test_in_sync_is_silent_noop(self, tmp_path, monkeypatch):
        asm = self._role_project(tmp_path)
        calls = self._wire(monkeypatch, asm, runtime=[], wt=[], git=[])
        res = asm.hooks.sync_hooks(result=_result([]))
        assert res.status == "in-sync"
        assert res.changes == ()
        assert calls == []

    def test_skipped_when_no_role(self, assembler):
        # plain project: no active/default role → skipped, no compose.
        assert assembler.hooks.sync_hooks(result=_result([])).status == "skipped"

    def test_heals_only_drifted_surface(self, tmp_path, monkeypatch):
        asm = self._role_project(tmp_path)
        calls = self._wire(
            monkeypatch,
            asm,
            runtime=[HookChange("runtime", "x", "content")],
            wt=[],
            git=[],
        )
        res = asm.hooks.sync_hooks(result=_result([]))
        assert res.status == "synced"
        assert HookChange("runtime", "x", "content") in res.changes
        assert set(calls) == {"rt_wire", "rt_bytes"}  # git/wt NOT healed

    def test_version_skew_refuses_but_names_drift(self, tmp_path, monkeypatch):
        asm = self._role_project(tmp_path)
        calls = self._wire(
            monkeypatch,
            asm,
            runtime=[HookChange("runtime", "x", "content")],
            wt=[],
            git=[],
            behind=True,
        )
        res = asm.hooks.sync_hooks(result=_result([]))
        assert res.status == "version-skew"
        assert HookChange("runtime", "x", "content") in res.changes  # named, not silent
        assert calls == []  # nothing healed from a stale binary
