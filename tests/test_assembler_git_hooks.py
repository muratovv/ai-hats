"""Tests for skill-contributed git hooks (HATS-088)."""

import stat
import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import (
    GITHOOKS_DIR,
    GITHOOKS_DISPATCHER_MARKER,
    GITHOOKS_MANIFEST,
    Assembler,
)
from ai_hats.models import ProjectConfig


pytestmark = pytest.mark.integration


def _git_init(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    # Local user config so commits would work if tests ever made any.
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(path), check=True)


def _git_get(path: Path, key: str) -> str:
    res = subprocess.run(
        ["git", "config", "--get", key],
        cwd=str(path),
        capture_output=True,
        text=True,
        check=False,
    )
    return res.stdout.strip() if res.returncode == 0 else ""


@pytest.fixture
def project_with_hook_skill(tmp_path):
    """Project + library with one skill that declares a pre-commit hook."""
    project = tmp_path / "project"
    project.mkdir()
    _git_init(project)

    lib = tmp_path / "lib"

    # Skill with a git_hooks declaration.
    skill_dir = lib / "skills" / "hook_skill"
    (skill_dir / "git_hooks").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Hook Skill")
    (skill_dir / "metadata.yaml").write_text(
        "name: hook_skill\n"
        "description: skill that ships a pre-commit hook\n"
        "git_hooks:\n"
        "  pre-commit:\n"
        "    - git_hooks/check.sh\n"
    )
    hook_script = skill_dir / "git_hooks" / "check.sh"
    hook_script.write_text("#!/usr/bin/env bash\necho 'check ran'\nexit 0\n")
    hook_script.chmod(0o755)

    # Trait that pulls the skill in.
    trait_dir = lib / "traits" / "trait-base"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text(
        "name: trait-base\n"
        "composition:\n"
        "  skills:\n"
        "    - hook_skill\n"
        "injection: Base.\n"
    )

    # Role using the trait.
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "priorities: [Quality]\n"
        "composition:\n"
        "  traits:\n"
        "    - trait-base\n"
        "injection: Role.\n"
    )

    config = ProjectConfig(provider="gemini", library_paths=[str(lib)])
    config.save(project / "ai-hats.yaml")

    return project, lib


@pytest.fixture
def project_no_hook_skill(tmp_path):
    """Project + library where the only skill has no git_hooks."""
    project = tmp_path / "project"
    project.mkdir()
    _git_init(project)
    lib = tmp_path / "lib"

    skill_dir = lib / "skills" / "plain_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Plain")
    (skill_dir / "metadata.yaml").write_text(
        "name: plain_skill\ndescription: no hooks\n"
    )

    trait_dir = lib / "traits" / "trait-base"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text(
        "name: trait-base\n"
        "composition:\n"
        "  skills:\n"
        "    - plain_skill\n"
        "injection: Base.\n"
    )

    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "priorities: [Quality]\n"
        "composition:\n"
        "  traits:\n"
        "    - trait-base\n"
        "injection: Role.\n"
    )

    config = ProjectConfig(provider="gemini", library_paths=[str(lib)])
    config.save(project / "ai-hats.yaml")
    return project, lib


# ----- Happy path -----


def test_install_creates_dispatcher_and_event_dir(project_with_hook_skill):
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    githooks = project / GITHOOKS_DIR
    assert githooks.is_dir()
    dispatcher = githooks / "pre-commit"
    assert dispatcher.is_file()
    assert GITHOOKS_DISPATCHER_MARKER in dispatcher.read_text()
    assert dispatcher.stat().st_mode & stat.S_IXUSR

    installed = githooks / "pre-commit.d" / "hook_skill-check.sh"
    assert installed.is_file()
    assert installed.stat().st_mode & stat.S_IXUSR
    assert "check ran" in installed.read_text()


def test_install_sets_core_hookspath(project_with_hook_skill):
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")
    assert _git_get(project, "core.hooksPath") == GITHOOKS_DIR


def test_manifest_written(project_with_hook_skill):
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")
    manifest = (project / GITHOOKS_DIR / GITHOOKS_MANIFEST).read_text()
    assert "pre-commit.d/hook_skill-check.sh" in manifest
    assert "pre-commit" in manifest


# ----- No-op when no skill declares hooks -----


def test_no_githooks_when_no_declarations(project_no_hook_skill):
    project, lib = project_no_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")
    assert not (project / GITHOOKS_DIR).exists()
    assert _git_get(project, "core.hooksPath") == ""


# ----- Idempotency -----


def test_reinstall_is_idempotent(project_with_hook_skill):
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")
    asm.set_role("test-role")  # second call

    event_d = project / GITHOOKS_DIR / "pre-commit.d"
    files = sorted(p.name for p in event_d.iterdir())
    assert files == ["hook_skill-check.sh"]


# ----- Stale removal -----


def test_stale_hooks_removed_when_skill_drops_declaration(project_with_hook_skill):
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")
    assert (project / GITHOOKS_DIR / "pre-commit.d" / "hook_skill-check.sh").exists()

    # Now mutate the skill to drop git_hooks and re-apply.
    metadata = lib / "skills" / "hook_skill" / "metadata.yaml"
    metadata.write_text("name: hook_skill\ndescription: now no hooks\n")
    asm.set_role("test-role")

    # Stale managed file gone.
    assert not (project / GITHOOKS_DIR / "pre-commit.d" / "hook_skill-check.sh").exists()


# ----- Conflict policies -----


def test_existing_dispatcher_without_marker_left_alone(project_with_hook_skill, capsys):
    project, lib = project_with_hook_skill
    githooks = project / GITHOOKS_DIR
    githooks.mkdir()
    foreign = githooks / "pre-commit"
    foreign.write_text("#!/usr/bin/env bash\n# user's own dispatcher\necho hi\n")
    foreign.chmod(0o755)

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    assert foreign.read_text().startswith("#!/usr/bin/env bash\n# user's own dispatcher")
    out = capsys.readouterr().out
    assert "not managed by ai-hats" in out


def test_existing_core_hookspath_other_value_left_alone(project_with_hook_skill, capsys):
    project, lib = project_with_hook_skill
    subprocess.run(
        ["git", "config", "core.hooksPath", "custom-hooks"],
        cwd=str(project), check=True,
    )

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    assert _git_get(project, "core.hooksPath") == "custom-hooks"
    out = capsys.readouterr().out
    assert "core.hooksPath is already set" in out


def test_unknown_event_silently_skipped(tmp_path):
    """A skill declaring an unknown git event is silently ignored.

    The framework only writes hooks for events listed in GIT_HOOK_EVENTS so a
    typo or future-event reference does not blow up the assembly.
    """
    project = tmp_path / "project"
    project.mkdir()
    _git_init(project)
    lib = tmp_path / "lib"

    skill_dir = lib / "skills" / "weird_skill"
    (skill_dir / "git_hooks").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Weird")
    (skill_dir / "metadata.yaml").write_text(
        "name: weird_skill\n"
        "git_hooks:\n"
        "  not-a-real-event:\n"
        "    - git_hooks/x.sh\n"
    )
    (skill_dir / "git_hooks" / "x.sh").write_text("#!/usr/bin/env bash\nexit 0\n")

    trait_dir = lib / "traits" / "trait-base"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text(
        "name: trait-base\n"
        "composition:\n"
        "  skills:\n"
        "    - weird_skill\n"
    )
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "composition:\n"
        "  traits:\n"
        "    - trait-base\n"
    )
    ProjectConfig(provider="gemini", library_paths=[str(lib)]).save(
        project / "ai-hats.yaml",
    )

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")
    # Nothing should have been installed because the only declared event was unknown.
    assert not (project / GITHOOKS_DIR).exists()


# ----- Dispatcher actually runs hooks -----


def test_dispatcher_runs_d_scripts(project_with_hook_skill, tmp_path):
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    dispatcher = project / GITHOOKS_DIR / "pre-commit"
    res = subprocess.run(
        [str(dispatcher)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "check ran" in res.stdout


def test_dispatcher_propagates_failure(project_with_hook_skill):
    project, lib = project_with_hook_skill

    # Replace the hook with one that fails.
    failing = lib / "skills" / "hook_skill" / "git_hooks" / "check.sh"
    failing.write_text("#!/usr/bin/env bash\necho boom >&2\nexit 7\n")

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    dispatcher = project / GITHOOKS_DIR / "pre-commit"
    res = subprocess.run([str(dispatcher)], capture_output=True, text=True)
    assert res.returncode == 7
    assert "boom" in res.stderr


# ----- HATS-593: sync_hooks drift-detecting re-materialization -----


def test_sync_hooks_noop_when_in_sync(project_with_hook_skill):
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    installed = project / GITHOOKS_DIR / "pre-commit.d" / "hook_skill-check.sh"
    before = installed.stat().st_mtime_ns

    res = asm.sync_hooks()
    assert res.status == "in-sync"
    # No rewrite when already consistent with source.
    assert installed.stat().st_mtime_ns == before


def test_sync_hooks_heals_corrupted_hook(project_with_hook_skill):
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    installed = project / GITHOOKS_DIR / "pre-commit.d" / "hook_skill-check.sh"
    installed.write_text("#!/usr/bin/env bash\n# DRIFTED\nexit 0\n")  # simulate drift

    res = asm.sync_hooks()
    assert res.status == "synced"
    body = installed.read_text()
    assert "check ran" in body
    assert "DRIFTED" not in body


def test_sync_hooks_heals_deleted_hook(project_with_hook_skill):
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    installed = project / GITHOOKS_DIR / "pre-commit.d" / "hook_skill-check.sh"
    installed.unlink()

    res = asm.sync_hooks()
    assert res.status == "synced"
    assert installed.is_file()
    assert "check ran" in installed.read_text()


# ----- HATS-593 Phase 1.3: version-skew guard (failure-mode #5) -----


def _write_update_cache(project, *, behind, ahead):
    """Seed the update-check cache so sync_hooks' version-skew guard reads it."""
    from datetime import datetime, timezone

    from ai_hats.update_check.cache import CacheEntry, write_cache

    write_cache(
        project,
        CacheEntry(
            checked_at=datetime.now(timezone.utc),
            installed_sha="a" * 40,
            latest_sha="b" * 40,
            remote_url="https://example.git",
            behind=behind,
            ahead=ahead,
        ),
    )


def test_sync_hooks_refuses_heal_when_binary_behind(project_with_hook_skill):
    """Installed binary strictly behind upstream → version-skew, no heal."""
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    installed = project / GITHOOKS_DIR / "pre-commit.d" / "hook_skill-check.sh"
    installed.write_text("#!/usr/bin/env bash\n# DRIFTED\nexit 0\n")  # plant drift
    _write_update_cache(project, behind=7, ahead=0)  # binary is behind upstream

    res = asm.sync_hooks()
    assert res.status == "version-skew"
    # Refused to materialize blind — the drifted content is left untouched.
    assert "DRIFTED" in installed.read_text()
    assert "self update" in res.detail


def test_sync_hooks_heals_when_binary_in_sync_with_upstream(project_with_hook_skill):
    """Cache says not-behind (ahead==0,behind==0) → normal heal proceeds."""
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    installed = project / GITHOOKS_DIR / "pre-commit.d" / "hook_skill-check.sh"
    installed.write_text("#!/usr/bin/env bash\n# DRIFTED\nexit 0\n")
    _write_update_cache(project, behind=0, ahead=0)

    res = asm.sync_hooks()
    assert res.status == "synced"
    assert "check ran" in installed.read_text()


def test_sync_hooks_heals_when_no_update_cache(project_with_hook_skill):
    """No cache (cold/bootstrap) → unknown skew → fail-open, heal proceeds."""
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    installed = project / GITHOOKS_DIR / "pre-commit.d" / "hook_skill-check.sh"
    installed.write_text("#!/usr/bin/env bash\n# DRIFTED\nexit 0\n")

    res = asm.sync_hooks()
    assert res.status == "synced"
    assert "check ran" in installed.read_text()


def test_sync_hooks_skips_non_git_project(tmp_path):
    """No .git/ → skipped, never raises."""
    project = tmp_path / "proj"
    project.mkdir()
    lib = tmp_path / "lib"
    skill_dir = lib / "skills" / "plain"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Plain")
    (skill_dir / "metadata.yaml").write_text("name: plain\ndescription: x\n")
    trait_dir = lib / "traits" / "trait-base"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - plain\ninjection: B.\n"
    )
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    ProjectConfig(provider="gemini", library_paths=[str(lib)]).save(project / "ai-hats.yaml")

    asm = Assembler(project, library_paths=[lib])
    res = asm.sync_hooks()
    assert res.status == "skipped"
