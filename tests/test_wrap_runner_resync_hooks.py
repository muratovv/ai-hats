"""HATS-707 → HATS-833: the in-session managed-hook drift net.

Re-homed from the dead ``session_start: [ai-hats self sync-hooks]`` lifecycle
channel to a direct ``Assembler.hooks.sync_hooks()`` call at session start in
``WrapRunner._resync_managed_hooks``, then generalized (HATS-833) from git-only
to ALL managed-hook surfaces (runtime + wt + git) with an observable heal note.

These tests drive ``WrapRunner._resync_managed_hooks()`` directly (the seam),
not a full PTY session.
"""

import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.hooks_manager import GITHOOKS_DIR
from ai_hats.models import ProjectConfig
from ai_hats.wrap_runner import WrapRunner

pytestmark = pytest.mark.integration


def _runner(project: Path) -> WrapRunner:
    """WrapRunner wired with the project's real HooksManager (HATS-865: the
    payload carries it; these seam tests exercise the result-less resync
    edge, so the composition itself is a placeholder)."""
    from ai_hats.composition_payload import CompositionPayload
    from ai_hats_core import CompositionResult

    payload = CompositionPayload(
        result=CompositionResult(
            name="t", priorities=[], rules=[], skills=[], injections=[],
        ),
        provider=None,
        effective_role="t",
        hooks=Assembler(project).hooks,
    )
    return WrapRunner(project, payload)




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


def test_resync_heals_drifted_hook_and_names_it(project_with_hook_role):
    project = project_with_hook_role
    installed = _installed_hook(project)
    installed.write_text("#!/usr/bin/env bash\n# DRIFTED\nexit 0\n")  # rebase/reset drift

    notices = _runner(project)._resync_managed_hooks()

    # Healed in place ...
    body = installed.read_text()
    assert "check ran" in body
    assert "DRIFTED" not in body
    # ... and observable (HATS-833 req-5): one NOTE naming the git surface + kind.
    assert len(notices) == 1
    note = notices[0]
    assert note.level == "note"
    assert "healed at start" in note.text
    assert "git-hook pre-commit" in note.text
    assert "content drift" in note.text


def test_resync_silent_when_in_sync(project_with_hook_role):
    """A clean (already in-sync) start emits NO notice and holds for nothing."""
    notices = _runner(project_with_hook_role)._resync_managed_hooks()
    assert notices == []


def test_resync_is_failopen_on_roleless_project(tmp_path):
    """No active role → sync_hooks skips; the net must never raise, no notice."""
    project = tmp_path / "plain"
    project.mkdir()
    _git_init(project)
    ProjectConfig(provider="gemini").save(project / "ai-hats.yaml")
    assert _runner(project)._resync_managed_hooks() == []


def test_resync_failure_returns_warn_notice_and_traces(tmp_path):
    """HATS-825/833: a failed resync is fail-open but no longer silent — it
    returns a WARN notice the caller surfaces in the pre-launch hold, and is
    persisted to the session trace (stderr alone is clobbered by the TUI)."""
    project = tmp_path / "plain"
    project.mkdir()
    _git_init(project)
    ProjectConfig(provider="gemini").save(project / "ai-hats.yaml")

    runner = _runner(project)
    runner.hooks.sync_hooks = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("boom")
    )
    session = runner.session_mgr.create_session()

    notices = runner._resync_managed_hooks(session)

    assert len(notices) == 1
    assert notices[0].level == "warn"
    assert "managed-hook resync failed" in notices[0].text
    assert "RuntimeError" in notices[0].text and "boom" in notices[0].text
    assert "managed-hook resync FAILED" in session.trace_path.read_text()
