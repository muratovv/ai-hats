"""Regression guard (HATS-1497): e2e files must use _helpers/git.py and conftest fixtures
rather than re-implementing local _git or installed_launcher scaffolding.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

E2E_DIR = Path(__file__).resolve().parent

# Files that carry a custom _git wrapper (e.g. custom env injection or specialized signature)
_GIT_EXEMPTIONS = {
    "test_githooks_coexistence.py",
    "test_githooks_orchestrator.py",
    "test_no_raw_destructive_multiline_marker.py",
    "test_root_residue_swept.py",
    "test_self_bump_unclaimed_sweep.py",
    "test_self_bump_v07_heal.py",
    "test_self_update_heals_legacy_refs.py",
    "test_wt_stale_ref_gate.py",
}


def test_e2e_no_local_git_or_launcher_scaffolding():
    """Pin the canonical Project / _helpers.git / conftest surface for new e2e tests.

    Refuses any raw subprocess `_git` or local `installed_launcher` definitions in `tests/e2e/`.
    """
    git_violations = []
    launcher_violations = []

    for test_file in sorted(E2E_DIR.glob("test_*.py")):
        if test_file.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(test_file.read_text())
        except Exception as exc:
            pytest.fail(f"Could not parse AST of {test_file.name}: {exc}")

        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                if node.name == "_git" and test_file.name not in _GIT_EXEMPTIONS:
                    code = ast.unparse(node)
                    if "subprocess.run" in code:
                        git_violations.append(test_file.name)
                elif node.name == "installed_launcher":
                    launcher_violations.append(test_file.name)

    assert not git_violations, f"Found raw subprocess _git definitions in e2e files: {git_violations}. Use `from _helpers.git import git` instead."
    assert not launcher_violations, f"Found local installed_launcher definitions in e2e files: {launcher_violations}. Use conftest's `installed_launcher` fixture."
