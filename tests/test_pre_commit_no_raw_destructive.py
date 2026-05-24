"""Unit tests for HATS-470 pre-commit-no-raw-destructive.sh.

Asserts the hook behaviour against a fixture src tree, not the real
ai_hats source — so the test is agnostic to ongoing refactors.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


HOOK_PATH = (
    Path(__file__).parent.parent
    / "library" / "core" / "skills" / "git-mastery"
    / "git_hooks" / "pre-commit-no-raw-destructive.sh"
)


def _make_fake_project(tmp_path: Path) -> Path:
    """Create a minimal git repo with a stub src/ai_hats/ tree."""
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    # Required git config or commit hooks bail.
    subprocess.run(
        ["git", "-C", str(project), "config", "user.email", "t@t"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(project), "config", "user.name", "t"],
        check=True,
    )
    src = project / "src" / "ai_hats"
    src.mkdir(parents=True)
    # safe_delete.py must exist so the whitelist passes — its raw ops
    # are intentional.
    (src / "safe_delete.py").write_text(
        "def discard(p):\n    p.unlink()\n"
    )
    return project


def _run_hook(cwd: Path) -> tuple[int, str, str]:
    """Invoke the hook from ``cwd``; return (returncode, stdout, stderr)."""
    env = os.environ.copy()
    env.pop("AI_HATS_NO_RAW_DESTRUCTIVE_SKIP", None)
    result = subprocess.run(
        ["bash", str(HOOK_PATH)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------- Behaviour ----------------------


def test_hook_exists_and_executable():
    assert HOOK_PATH.is_file(), f"hook not found at {HOOK_PATH}"
    # File mode bit 0o100 = owner-execute.
    assert os.access(HOOK_PATH, os.X_OK), "hook is not executable"


def test_hook_passes_on_clean_tree(tmp_path):
    project = _make_fake_project(tmp_path)
    rc, _, err = _run_hook(project)
    assert rc == 0, f"hook failed on clean tree: {err}"


def test_hook_blocks_raw_unlink_outside_safe_delete(tmp_path):
    project = _make_fake_project(tmp_path)
    (project / "src" / "ai_hats" / "bad.py").write_text(
        "def f(p):\n    p.unlink()\n"
    )
    rc, _, err = _run_hook(project)
    assert rc == 1
    assert "raw destructive call" in err
    assert "bad.py" in err


def test_hook_blocks_raw_rmtree(tmp_path):
    project = _make_fake_project(tmp_path)
    (project / "src" / "ai_hats" / "bad.py").write_text(
        "import shutil\n"
        "def f(p):\n    shutil.rmtree(p)\n"
    )
    rc, _, err = _run_hook(project)
    assert rc == 1
    assert "rmtree" in err or "bad.py" in err


def test_hook_blocks_raw_rmdir(tmp_path):
    project = _make_fake_project(tmp_path)
    (project / "src" / "ai_hats" / "bad.py").write_text(
        "def f(p):\n    p.rmdir()\n"
    )
    rc, _, err = _run_hook(project)
    assert rc == 1


def test_hook_allows_inline_marker_bypass(tmp_path):
    project = _make_fake_project(tmp_path)
    (project / "src" / "ai_hats" / "ok.py").write_text(
        "def f(p):\n"
        "    p.unlink()  # safe-delete: ok empty-config\n"
        "    p.rmdir()  # safe-delete: ok empty-dir\n"
    )
    rc, _, err = _run_hook(project)
    assert rc == 0, f"inline-marker bypass should pass; got: {err}"


def test_hook_noop_on_non_ai_hats_project(tmp_path):
    """Project without src/ai_hats/ → silent no-op."""
    project = tmp_path / "other"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "main.py").write_text(
        "def f(p):\n    p.unlink()\n"
        "import shutil\n"
        "shutil.rmtree('/anywhere')\n"
    )
    rc, _, err = _run_hook(project)
    assert rc == 0, (
        f"hook must no-op for non-ai-hats projects; "
        f"got returncode={rc}, stderr={err!r}"
    )


def test_hook_respects_skip_env(tmp_path):
    project = _make_fake_project(tmp_path)
    (project / "src" / "ai_hats" / "bad.py").write_text(
        "def f(p):\n    p.unlink()\n"
    )
    env = os.environ.copy()
    env["AI_HATS_NO_RAW_DESTRUCTIVE_SKIP"] = "1"
    result = subprocess.run(
        ["bash", str(HOOK_PATH)],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "skipped" in result.stderr


def test_hook_does_not_flag_safe_delete_module_itself(tmp_path):
    """safe_delete.py is the single legitimate raw-ops site → must not flag."""
    project = _make_fake_project(tmp_path)
    # Add more raw ops to safe_delete.py — should still pass.
    (project / "src" / "ai_hats" / "safe_delete.py").write_text(
        "import shutil\n"
        "def discard(p):\n"
        "    p.unlink()\n"
        "    p.rmdir()\n"
        "    shutil.rmtree(p)\n"
    )
    rc, _, err = _run_hook(project)
    assert rc == 0, f"safe_delete.py raw ops must not trip the hook; got: {err}"
