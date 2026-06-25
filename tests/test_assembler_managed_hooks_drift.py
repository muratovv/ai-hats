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
from ai_hats.hooks_manager import HookChange
from ai_hats.composer import CompositionResult, ResolvedComponent
from ai_hats.models import ComponentType, ProjectConfig
from ai_hats.paths import (
    hooks_dir,
    managed_runtime_hook_filename,
    managed_wt_hook_filename,
    wt_hooks_dir,
)
from ai_hats.providers import ClaudeProvider


# ----- fixtures / helpers -----


@pytest.fixture
def assembler(tmp_path: Path) -> Assembler:
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    return Assembler(project_dir=project)


def _result(skills: list[ResolvedComponent]) -> CompositionResult:
    return CompositionResult(
        name="r", priorities=[], rules=[], skills=skills, injections=[]
    )


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
    return ResolvedComponent(name=name, component_type=ComponentType.SKILL, source_path=d)


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
    return ResolvedComponent(name=name, component_type=ComponentType.SKILL, source_path=d)


# ----- runtime-hook BYTES drift -----


class TestRuntimeBytesDrift:
    def test_in_sync_after_materialize(self, assembler, tmp_path):
        s = _skill_runtime(tmp_path / "sk", "sa", "PreToolUse", "Bash", "h/a.sh")
        res = _result([s])
        assembler.hooks.materialize_runtime_hooks(res)
        assert assembler.hooks._runtime_bytes_changes(res) == []

    def test_missing_script_reported(self, assembler, tmp_path):
        s = _skill_runtime(tmp_path / "sk", "sa", "PreToolUse", "Bash", "h/a.sh")
        res = _result([s])
        assembler.hooks.materialize_runtime_hooks(res)
        dest = hooks_dir(assembler.project_dir) / managed_runtime_hook_filename(
            "sa", "h/a.sh"
        )
        dest.unlink()
        changes = assembler.hooks._runtime_bytes_changes(res)
        assert (dest.name, "missing") in changes

    def test_content_drift_reported(self, assembler, tmp_path):
        s = _skill_runtime(tmp_path / "sk", "sa", "PreToolUse", "Bash", "h/a.sh")
        res = _result([s])
        assembler.hooks.materialize_runtime_hooks(res)
        dest = hooks_dir(assembler.project_dir) / managed_runtime_hook_filename(
            "sa", "h/a.sh"
        )
        dest.write_text("#!/usr/bin/env bash\necho drifted\n")
        changes = assembler.hooks._runtime_bytes_changes(res)
        assert (dest.name, "content") in changes

    def test_stale_reported_when_skill_leaves(self, assembler, tmp_path):
        s = _skill_runtime(tmp_path / "sk", "sa", "PreToolUse", "Bash", "h/a.sh")
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
            assert ("shared_state_classifier.sh", "missing") in assembler.hooks._runtime_bytes_changes(res)


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


# ----- runtime-hook WIRING drift (.claude/settings.json) -----


class TestRuntimeWiringDrift:
    def _claude_project(self, tmp_path: Path) -> Assembler:
        project = tmp_path / "proj"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        ProjectConfig(provider="claude").save(project / "ai-hats.yaml")
        return Assembler(project_dir=project)

    def test_in_sync_after_ensure(self, tmp_path):
        asm = self._claude_project(tmp_path)
        s = _skill_runtime(tmp_path / "sk", "mf", "PostToolUse", "Write", "h/f.sh")
        res = _result([s])
        prov = ClaudeProvider()
        prov.ensure_runtime_hooks(asm.project_dir, res)
        assert prov.runtime_wiring_changes(asm.project_dir, res) == []

    def test_unwired_managed_entry_reported(self, tmp_path):
        asm = self._claude_project(tmp_path)
        s = _skill_runtime(tmp_path / "sk", "mf", "PostToolUse", "Write", "h/f.sh")
        res = _result([s])
        prov = ClaudeProvider()
        prov.ensure_runtime_hooks(asm.project_dir, res)
        # Strip the skill's managed PostToolUse entry → wiring drift.
        sp = asm.project_dir / ".claude" / "settings.json"
        data = json.loads(sp.read_text())
        data["hooks"]["PostToolUse"] = []
        sp.write_text(json.dumps(data))
        names = [n for n, kind in prov.runtime_wiring_changes(asm.project_dir, res)]
        assert any("mf" in n for n in names)

    def test_user_entry_wiring_same_script_is_not_drift(self, tmp_path):
        """Review pt-1: a user-authored entry already wiring the same script
        basename suppresses the managed entry — the detector must inherit that
        and NOT report perpetual drift."""
        asm = self._claude_project(tmp_path)
        s = _skill_runtime(tmp_path / "sk", "mf", "PostToolUse", "Write", "h/f.sh")
        res = _result([s])
        prov = ClaudeProvider()
        prov.ensure_runtime_hooks(asm.project_dir, res)  # writes guard + skill entry
        sp = asm.project_dir / ".claude" / "settings.json"
        data = json.loads(sp.read_text())
        # Replace the skill's MANAGED PostToolUse entry with a USER entry wiring
        # the same script basename (no _ai_hats_managed tag).
        basename = managed_runtime_hook_filename("mf", "h/f.sh")
        data["hooks"]["PostToolUse"] = [
            {"matcher": "Write", "hooks": [{"type": "command", "command": f"./{basename}"}]}
        ]
        sp.write_text(json.dumps(data))
        # Guard (PreToolUse) is untouched → only the skill hook is at stake, and
        # the user covers it → NO wiring drift at all.
        assert prov.runtime_wiring_changes(asm.project_dir, res) == []


# ----- sync_hooks orchestration (drift-gate / version-skew / per-surface heal) -----


class TestSyncHooksOrchestration:
    def _role_project(self, tmp_path: Path) -> Assembler:
        project = tmp_path / "proj"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        ProjectConfig(provider="claude", active_role="r", default_role="r").save(
            project / "ai-hats.yaml"
        )
        return Assembler(project_dir=project)

    def _wire(self, monkeypatch, asm, *, runtime, wt, git, behind=False):
        calls: list[str] = []
        monkeypatch.setattr(asm.hooks, "_runtime_hooks_changes", lambda result, provider: runtime)
        monkeypatch.setattr(asm.hooks, "_wt_hooks_changes", lambda result: wt)
        monkeypatch.setattr(asm.hooks, "_git_hooks_changes", lambda result: git)
        monkeypatch.setattr(asm.hooks, "_binary_behind_source", lambda: behind)
        monkeypatch.setattr(
            asm.hooks, "materialize_runtime_hooks", lambda result: calls.append("rt_bytes")
        )
        monkeypatch.setattr(
            asm.hooks, "materialize_worktree_hooks", lambda result: calls.append("wt")
        )
        monkeypatch.setattr(asm.hooks, "install_git_hooks", lambda result: calls.append("git"))
        monkeypatch.setattr(
            "ai_hats.providers.ClaudeProvider.ensure_runtime_hooks",
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
