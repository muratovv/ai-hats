"""HATS-707: the in-session git-hook drift net (HATS-593 layer B).

The maintainer's ``session_start: [ai-hats self sync-hooks]`` lifecycle-hook
declaration never executed — the ``hooks:`` composition channel had zero
runtime consumers (the dead channel deleted in HATS-707). Its one piece of
real intent — re-heal git-hook drift that layer A (post-merge/post-checkout)
misses (rebase / reset --hard / stash / manual edits) — is re-homed to a
direct ``Assembler.sync_hooks()`` call at session start in ``WrapRunner``.

These tests drive ``WrapRunner._resync_git_hooks()`` directly (the seam),
not a full PTY session.
"""

import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.githooks import GITHOOKS_DIR
from ai_hats.models import ProjectConfig
from ai_hats.wrap_runner import WrapRunner

pytestmark = pytest.mark.integration


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(path), check=True)


@pytest.fixture
def project_with_hook_role(tmp_path):
    """Git project + library with a role whose skill ships a pre-commit hook."""
    project = tmp_path / "project"
    project.mkdir()
    _git_init(project)

    lib = tmp_path / "lib"
    skill_dir = lib / "skills" / "hook_skill"
    (skill_dir / "git_hooks").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: hook_skill\n"
        "description: skill that ships a pre-commit hook\n"
        "ai_hats:\n"
        "  git_hooks:\n"
        "    pre-commit:\n"
        "      - git_hooks/check.sh\n"
        "---\n"
        "# Hook Skill\n"
    )
    hook_script = skill_dir / "git_hooks" / "check.sh"
    hook_script.write_text("#!/usr/bin/env bash\necho 'check ran'\nexit 0\n")
    hook_script.chmod(0o755)

    trait_dir = lib / "traits" / "trait-base"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - hook_skill\ninjection: Base.\n"
    )

    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\npriorities: [Quality]\ncomposition:\n  traits:\n    - trait-base\ninjection: Role.\n"
    )

    config = ProjectConfig(provider="gemini", library_paths=[str(lib)])
    config.save(project / "ai-hats.yaml")

    # Install hooks + persist active role so WrapRunner(project) resolves both
    # from ai-hats.yaml (it builds Assembler(project_dir) without explicit paths).
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")
    return project


def _installed_hook(project: Path) -> Path:
    return project / GITHOOKS_DIR / "pre-commit.d" / "hook_skill-check.sh"


def test_resync_heals_drifted_hook_at_session_start(project_with_hook_role):
    project = project_with_hook_role
    installed = _installed_hook(project)
    installed.write_text("#!/usr/bin/env bash\n# DRIFTED\nexit 0\n")  # simulate rebase/reset drift

    WrapRunner(project)._resync_git_hooks()

    body = installed.read_text()
    assert "check ran" in body
    assert "DRIFTED" not in body


def test_resync_is_failopen_on_non_git(tmp_path):
    """No .git → sync_hooks skips; the net must never raise at session start."""
    project = tmp_path / "plain"
    project.mkdir()
    ProjectConfig(provider="gemini").save(project / "ai-hats.yaml")
    # Must not raise; clean resync returns no warning.
    assert WrapRunner(project)._resync_git_hooks() is None


def test_resync_failure_returns_summary_and_traces(tmp_path, monkeypatch):
    """HATS-825: a failed resync is fail-open but no longer silent.

    The failure must (a) not raise, (b) return a one-line summary the caller
    surfaces in the pre-launch hold, and (c) be persisted to the session trace
    — previously it reached only stderr, which the TUI clobbers.
    """
    project = tmp_path / "plain"
    project.mkdir()
    ProjectConfig(provider="gemini").save(project / "ai-hats.yaml")

    runner = WrapRunner(project)
    monkeypatch.setattr(
        runner.assembler,
        "sync_hooks",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    session = runner.session_mgr.create_session()

    summary = runner._resync_git_hooks(session)

    assert summary is not None
    assert "git-hook resync failed" in summary
    assert "RuntimeError" in summary and "boom" in summary
    assert "git-hook resync FAILED" in session.trace_path.read_text()
