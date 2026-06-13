"""Tests for skill-contributed git hooks (HATS-088)."""

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.githooks import (  # HATS-715: git-hook constants moved to githooks.py
    GITHOOKS_DIR,
    GITHOOKS_DISPATCHER_MARKER,
    GITHOOKS_MANIFEST,
)
from ai_hats.models import GIT_HOOK_EVENTS, ProjectConfig


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


# ----- HATS-593 Phase 1.4: post-merge / post-checkout self-heal events -----


def test_post_merge_and_post_checkout_are_registered_events():
    """The drift-introducing events are recognized by the framework."""
    assert "post-merge" in GIT_HOOK_EVENTS
    assert "post-checkout" in GIT_HOOK_EVENTS


@pytest.fixture
def project_with_self_heal_skill(tmp_path):
    """Project + library with a skill that declares the self-heal hooks
    for both post-merge and post-checkout (mirrors the real git-mastery
    declaration)."""
    project = tmp_path / "project"
    project.mkdir()
    _git_init(project)

    lib = tmp_path / "lib"
    skill_dir = lib / "skills" / "healer_skill"
    (skill_dir / "git_hooks").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Healer")
    (skill_dir / "metadata.yaml").write_text(
        "name: healer_skill\n"
        "description: ships post-merge/post-checkout self-heal hooks\n"
        "git_hooks:\n"
        "  post-merge:\n"
        "    - git_hooks/self-heal.sh\n"
        "  post-checkout:\n"
        "    - git_hooks/self-heal.sh\n"
    )
    # A self-heal script that mirrors the real one's branch-flag guard so the
    # behavioural test below is meaningful.
    script = skill_dir / "git_hooks" / "self-heal.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'set -uo pipefail\n'
        'EVENT="${AI_HATS_HOOK_EVENT:-$(basename "$0")}"\n'
        'if [[ "$EVENT" == "post-checkout" ]]; then\n'
        '    flag="${3:-0}"\n'
        '    [[ "$flag" != "1" ]] && exit 0\n'
        'fi\n'
        'echo "HEALED:$EVENT"\n'
        "exit 0\n"
    )
    script.chmod(0o755)

    trait_dir = lib / "traits" / "trait-base"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - healer_skill\ninjection: B.\n"
    )
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    ProjectConfig(provider="gemini", library_paths=[str(lib)]).save(project / "ai-hats.yaml")
    return project, lib


def test_composition_installs_both_self_heal_hooks(project_with_self_heal_skill):
    """Composition installs dispatcher + .d/ script for BOTH events."""
    project, lib = project_with_self_heal_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    githooks = project / GITHOOKS_DIR
    for event in ("post-merge", "post-checkout"):
        dispatcher = githooks / event
        assert dispatcher.is_file(), f"{event} dispatcher not installed"
        assert GITHOOKS_DISPATCHER_MARKER in dispatcher.read_text()
        assert dispatcher.stat().st_mode & stat.S_IXUSR
        script = githooks / f"{event}.d" / "healer_skill-self-heal.sh"
        assert script.is_file(), f"{event}.d script not installed"
        assert script.stat().st_mode & stat.S_IXUSR

    manifest = (githooks / GITHOOKS_MANIFEST).read_text()
    assert "post-merge.d/healer_skill-self-heal.sh" in manifest
    assert "post-checkout.d/healer_skill-self-heal.sh" in manifest


def test_post_checkout_noops_on_file_checkout(project_with_self_heal_skill):
    """post-checkout with branch-flag 0 (file checkout) must NOT self-heal."""
    project, lib = project_with_self_heal_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    dispatcher = project / GITHOOKS_DIR / "post-checkout"
    # git passes: prev_head new_head branch_flag. flag 0 = file checkout.
    res = subprocess.run(
        [str(dispatcher), "abc", "def", "0"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "HEALED" not in res.stdout

    # flag 1 = branch checkout → self-heal fires.
    res = subprocess.run(
        [str(dispatcher), "abc", "def", "1"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "HEALED:post-checkout" in res.stdout


# ----- HATS-593 Phase 2: dispatcher fail-closed backstop -----


def test_dispatcher_blocks_when_managed_hook_deleted(project_with_hook_skill):
    """A manifest-expected managed .d/ hook gone → dispatcher fails closed."""
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    githooks = project / GITHOOKS_DIR
    managed = githooks / "pre-commit.d" / "hook_skill-check.sh"
    assert managed.is_file()
    managed.unlink()  # the worst case: self-heal failed AND the hook is gone

    res = subprocess.run([str(githooks / "pre-commit")], capture_output=True, text=True)
    assert res.returncode == 1
    assert "corrupt" in res.stderr
    assert "ai-hats self init" in res.stderr


def test_dispatcher_blocks_when_managed_hook_non_executable(project_with_hook_skill):
    """A manifest-expected managed hook that lost its exec bit → fail closed."""
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    githooks = project / GITHOOKS_DIR
    managed = githooks / "pre-commit.d" / "hook_skill-check.sh"
    managed.chmod(0o644)  # strip exec bit

    res = subprocess.run([str(githooks / "pre-commit")], capture_output=True, text=True)
    assert res.returncode == 1
    assert "corrupt" in res.stderr


def test_dispatcher_runs_clean_when_all_managed_hooks_present(project_with_hook_skill):
    """Counter-test: nothing missing → dispatcher runs the hook, exits 0."""
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    githooks = project / GITHOOKS_DIR
    res = subprocess.run([str(githooks / "pre-commit")], capture_output=True, text=True)
    assert res.returncode == 0
    assert "check ran" in res.stdout
    assert "corrupt" not in res.stderr


def test_dispatcher_does_not_block_event_with_no_managed_entries(project_with_hook_skill):
    """Counter-test (scope): an event whose .d/ holds no MANAGED entries is
    NOT blocked by the backstop — only manifest-listed managed hooks gate."""
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    githooks = project / GITHOOKS_DIR
    # Hand-install a dispatcher for an event with NO managed .d/ entries
    # (manifest lists only pre-commit hooks). The dispatcher is event-agnostic;
    # copy the real one and name it for an unmanaged event.
    pre_push = githooks / "pre-push"
    shutil.copy2(githooks / "pre-commit", pre_push)
    pre_push.chmod(0o755)

    res = subprocess.run([str(pre_push)], capture_output=True, text=True)
    # No pre-push.d/* in the manifest → backstop inert → normal empty-.d/ no-op.
    assert res.returncode == 0
    assert "corrupt" not in res.stderr


# ----- HATS-617: skill-lint-gate is scoped to skill-authoring roles --------

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _composed_skill_names(role: str) -> set[str]:
    """Skills resolved for `role` against THIS checkout's library.

    Explicit `library_paths` (not the builtin install location) so the test
    reads the library it ships with — correct under an editable install made
    from a different checkout (e.g. a worktree) as well as in CI.
    """
    asm = Assembler(
        _REPO_ROOT,
        library_paths=[_REPO_ROOT / "library" / "core", _REPO_ROOT / "library" / "usage"],
    )
    result = asm.composer.compose(role, overlay=asm._get_overlay(role))
    assert result.errors == [], result.errors
    return {s.name for s in result.skills}


def test_skill_lint_gate_present_for_skill_authoring_roles():
    """maintainer + role-curator carry the `skill-engineer` trait, so the
    pre-commit gate composes into both."""
    for role in ("maintainer", "role-curator"):
        names = _composed_skill_names(role)
        assert "skill-lint-gate" in names, f"{role} missing skill-lint-gate"
        # Positive control: an existing skill-engineer skill resolves too, so a
        # False above would mean "absent", not "composition short-circuited".
        assert "skill-template" in names, f"{role} pos-control skill-template missing"


def test_skill_lint_gate_absent_from_non_authoring_roles():
    """A role without the `skill-engineer` trait must NOT receive the gate."""
    for role in ("assistant", "architect"):
        assert "skill-lint-gate" not in _composed_skill_names(role), (
            f"{role} unexpectedly received skill-lint-gate"
        )
