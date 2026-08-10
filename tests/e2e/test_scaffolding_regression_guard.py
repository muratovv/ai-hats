"""e2e (HATS-1497)

flow:   a maintainer running e2e test suite regression checks
cmds:
    bash scripts/run-e2e-gate.sh
expect: regression guard verifies all e2e test files use git helpers and conftest
        fixtures
why: without scaffolding regression guards, e2e tests introduce raw subprocess calls
     that leak state"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

E2E_DIR = Path(__file__).resolve().parent

# R8: Per-file explicit justification for remaining raw subprocess git helpers.
# Any module-level git helper that spawns raw subprocesses must be documented here with its reason.
_GIT_EXEMPTIONS: dict[str, str] = {
    "test_githooks_coexistence.py": "Tests git hook coexistence with custom env overrides and non-zero exit validation.",
    "test_githooks_orchestrator.py": "Tests git hook orchestration with isolated environment variables.",
    "test_no_raw_destructive_multiline_marker.py": "Tests raw destructive marker scanning capturing stderr/stdout output.",
    "test_root_residue_swept.py": "Tests root residue sweeping with explicit environment isolation.",
    "test_self_bump_unclaimed_sweep.py": "Tests custom git log counting and unclaimed version sweeping.",
    "test_self_bump_v07_heal.py": "Tests custom v0.7 version bump healing and log counting.",
    "test_self_update_heals_legacy_refs.py": "Tests legacy git ref healing with custom git ref operations.",
    "test_wt_stale_ref_gate.py": "Tests worktree stale ref gate with direct raw git process execution.",
    "test_launcher_worktree_execution.py": "Tests launcher worktree execution with custom commit tree.",
    "test_prepush_e2e_master_gate.py": "Tests pre-push master gate with custom GIT_CONFIG_GLOBAL isolation.",
    "test_session_cache_out_of_tree.py": "Tests out-of-tree session cache with custom git status output parsing.",
    "test_broken_hook_ref_startup_warn.py": "Tests broken hook ref startup warning with raw git process.",
}

_PROCESS_ATTRS = {"run", "Popen", "check_output", "check_call", "call", "system", "popen"}


def _calls_raw_subprocess(node: ast.AST) -> bool:
    """R8 (3): AST inspection for subprocess/os process spawning calls in function body."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            func = n.func
            if isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Name) and func.value.id in ("subprocess", "os"):
                    if func.attr in _PROCESS_ATTRS:
                        return True
            elif isinstance(func, ast.Name) and func.id in ("subprocess_run", "system", "popen"):
                return True
    return False


def test_e2e_no_local_git_or_launcher_scaffolding():
    """Pin the canonical Project / _helpers.git / conftest surface for new e2e tests.

    R8: Refuses any unexempted module-level git helper that spawns raw subprocesses, or local `installed_launcher` definitions in `tests/e2e/`.
    """
    git_violations: list[str] = []
    launcher_violations: list[str] = []

    for test_file in sorted(E2E_DIR.glob("test_*.py")):
        if test_file.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(test_file.read_text())
        except Exception as exc:
            pytest.fail(f"Could not parse AST of {test_file.name}: {exc}")

        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("test_"):
                # R8 (1): Match any module-level def whose name contains 'git'
                if "git" in node.name.lower():
                    if _calls_raw_subprocess(node) and test_file.name not in _GIT_EXEMPTIONS:
                        git_violations.append(f"{test_file.name}:{node.name}")
                elif node.name == "installed_launcher":
                    launcher_violations.append(test_file.name)

    assert not git_violations, (
        f"Found unexempted raw subprocess git definitions in e2e files: {git_violations}. "
        f"Use `from _helpers.git import git` or justify in _GIT_EXEMPTIONS."
    )
    assert not launcher_violations, (
        f"Found local installed_launcher definitions in e2e files: {launcher_violations}. "
        f"Use conftest's `installed_launcher` fixture."
    )
