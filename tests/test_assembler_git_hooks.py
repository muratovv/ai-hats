"""Tests for the git orchestrator (HATS-088; rebuilt for HATS-1337).

What `install_git_hooks` puts on disk is now ONE artifact per declared event —
the dispatcher — and nothing else. The gate scripts it used to flatten-copy into
`.githooks/<event>.d/<skill>-<basename>`, the `.ai-hats-manifest` that tracked
them and the drift arm that policed them are retired: gates are resolved live
from the composition at commit time (ADR-0020 D3).

Dispatcher *behaviour* lives in `test_githooks_dispatcher.py`; this file is
about installation, conflict policy and `core.hooksPath`.
"""

import stat
import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.hooks_manager import (
    GITHOOKS_BYPASS_JOURNAL,
    GITHOOKS_DIR,
    GITHOOKS_DISPATCHER_MARKER,
    GITHOOKS_MANIFEST,
    install_git_hooks,
)
from ai_hats.materialize import compose_to_report
from ai_hats.models import GIT_HOOK_EVENTS, ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.integration


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=str(path), check=True, capture_output=True)
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
        "name: test-role\n"
        "priorities: [Quality]\n"
        "composition:\n"
        "  traits:\n"
        "    - trait-base\n"
        "injection: Role.\n"
    )

    ProjectConfig(provider="agy", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)
    return project, lib


@pytest.fixture
def composed(project_with_hook_skill):
    """The fixture project after a real composition."""
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")
    return project, lib, asm


# ----- what lands on disk -----


def test_install_writes_the_dispatcher_and_nothing_else(composed):
    """R3: the orchestrator is the only durable artifact in `.githooks/`."""
    project, _lib, _asm = composed
    githooks = project / GITHOOKS_DIR

    dispatcher = githooks / "pre-commit"
    assert dispatcher.is_file()
    assert GITHOOKS_DISPATCHER_MARKER in dispatcher.read_text()
    assert dispatcher.stat().st_mode & stat.S_IXUSR

    assert sorted(p.name for p in githooks.iterdir()) == ["pre-commit"], (
        "no <event>.d/, no manifest, no bypass-journal copy — only the dispatcher"
    )


def test_no_gate_copies_and_no_manifest(composed):
    """The retired layer, named explicitly so a reintroduction goes red."""
    project, _lib, _asm = composed
    githooks = project / GITHOOKS_DIR

    assert not (githooks / "pre-commit.d").exists()
    assert not (githooks / GITHOOKS_MANIFEST).exists()
    assert not (githooks / GITHOOKS_BYPASS_JOURNAL).exists()


def test_no_githooks_when_no_declarations(tmp_path):
    """A project whose skills declare no git hooks keeps a clean root."""
    project = tmp_path / "project"
    project.mkdir()
    _git_init(project)
    lib = tmp_path / "lib"

    skill_dir = lib / "skills" / "plain_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: plain_skill\ndescription: no hooks\n---\n# P\n")
    (lib / "traits" / "trait-base").mkdir(parents=True)
    (lib / "traits" / "trait-base" / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - plain_skill\ninjection: Base.\n"
    )
    (lib / "roles" / "test-role").mkdir(parents=True)
    (lib / "roles" / "test-role" / "config.yaml").write_text(
        "name: test-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: Role.\n"
    )
    ProjectConfig(provider="agy", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    assert not (project / GITHOOKS_DIR).exists()
    assert _git_get(project, "core.hooksPath") == ""


def test_reinstall_is_idempotent(composed):
    project, _lib, asm = composed
    asm.set_role("test-role")

    githooks = project / GITHOOKS_DIR
    assert sorted(p.name for p in githooks.iterdir()) == ["pre-commit"]


def test_dispatcher_dropped_when_the_skill_drops_its_declaration(composed):
    """Ownership is proven by the marker the dispatcher carries, not a manifest."""
    project, lib, asm = composed
    assert (project / GITHOOKS_DIR / "pre-commit").is_file()

    (lib / "skills" / "hook_skill" / "SKILL.md").write_text(
        "---\nname: hook_skill\ndescription: now no hooks\n---\n# Hook Skill\n"
    )
    asm.set_role("test-role")

    assert not (project / GITHOOKS_DIR / "pre-commit").exists()


def test_unknown_event_silently_skipped(tmp_path):
    """A typo or future event must not blow up assembly (GIT_HOOK_EVENTS gate)."""
    project = tmp_path / "project"
    project.mkdir()
    _git_init(project)
    lib = tmp_path / "lib"

    skill_dir = lib / "skills" / "weird_skill"
    (skill_dir / "git_hooks").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: weird_skill\ndescription: bad event\nai_hats:\n  git_hooks:\n"
        "    pre-telepathy:\n      - git_hooks/x.sh\n---\n# W\n"
    )
    (skill_dir / "git_hooks" / "x.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (lib / "traits" / "trait-base").mkdir(parents=True)
    (lib / "traits" / "trait-base" / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - weird_skill\ninjection: Base.\n"
    )
    (lib / "roles" / "test-role").mkdir(parents=True)
    (lib / "roles" / "test-role" / "config.yaml").write_text(
        "name: test-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: Role.\n"
    )
    ProjectConfig(provider="agy", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    assert not (project / GITHOOKS_DIR).exists()


def test_post_merge_and_post_checkout_are_registered_events():
    assert "post-merge" in GIT_HOOK_EVENTS
    assert "post-checkout" in GIT_HOOK_EVENTS


# ----- core.hooksPath (R1) -----


def test_core_hookspath_is_absolute(composed):
    """Z1: git resolves a relative value against the working tree it runs in,
    and `.githooks/` is generated + gitignored, so it never exists in a linked
    worktree. Absolute is what gates every worktree from one shared config."""
    project, _lib, _asm = composed
    value = _git_get(project, "core.hooksPath")

    assert Path(value).is_absolute(), value
    assert Path(value) == (project / GITHOOKS_DIR).resolve()


def test_a_worktree_resolves_the_same_hooks_dir(composed):
    """The Z1 regression, asserted where git itself answers it."""
    project, _lib, _asm = composed
    subprocess.run(
        ["git", "commit", "-qm", "seed", "--allow-empty"],
        cwd=str(project),
        check=True,
        capture_output=True,
    )
    worktree = project.parent / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "task/x", str(worktree)],
        cwd=str(project),
        check=True,
        capture_output=True,
    )

    resolved = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert (Path(resolved) / "pre-commit").is_file(), (
        f"a commit in the worktree would find no hooks at {resolved!r}"
    )
    assert Path(resolved).resolve() == (project / GITHOOKS_DIR).resolve()


def test_a_pre_1337_relative_hookspath_is_migrated_quietly(project_with_hook_skill, capsys):
    """Migration, not takeover: the directory never moved, so no warning and no
    `previousHooksPath` record (which would self-point and be chained into)."""
    project, lib = project_with_hook_skill
    subprocess.run(["git", "config", "core.hooksPath", GITHOOKS_DIR], cwd=str(project), check=True)

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    capsys.readouterr()
    asm.set_role("test-role")

    assert Path(_git_get(project, "core.hooksPath")).is_absolute()
    assert _git_get(project, "ai-hats.previousHooksPath") == ""
    assert "taken over" not in capsys.readouterr().out


def test_an_already_absolute_hookspath_is_left_alone(project_with_hook_skill, capsys):
    project, lib = project_with_hook_skill
    absolute = str((project / GITHOOKS_DIR).resolve())
    subprocess.run(["git", "config", "core.hooksPath", absolute], cwd=str(project), check=True)

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    capsys.readouterr()
    asm.set_role("test-role")

    assert _git_get(project, "core.hooksPath") == absolute
    assert "taken over" not in capsys.readouterr().out


# ----- conflict policy -----


def test_a_foreign_hookspath_is_taken_over_and_recorded(project_with_hook_skill, capsys):
    """HATS-999: the displaced dir is recorded so the dispatcher chains to it."""
    project, lib = project_with_hook_skill
    subprocess.run(
        ["git", "config", "core.hooksPath", "custom-hooks"], cwd=str(project), check=True
    )

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    capsys.readouterr()
    asm.set_role("test-role")

    assert Path(_git_get(project, "core.hooksPath")).is_absolute()
    assert _git_get(project, "ai-hats.previousHooksPath") == "custom-hooks"
    assert "taken over" in capsys.readouterr().out


def test_existing_dispatcher_without_marker_left_alone(project_with_hook_skill, capsys):
    """A hand-written `pre-commit` is somebody's work — never overwritten."""
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
    assert "not managed by ai-hats" in capsys.readouterr().out


def test_set_role_routes_hooks_warning_to_sink(project_with_hook_skill, capsys):
    """HATS-970: warnings ride the sink for the HITL read-hold, not stdout."""
    project, lib = project_with_hook_skill
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    subprocess.run(
        ["git", "config", "core.hooksPath", "custom-hooks"], cwd=str(project), check=True
    )
    capsys.readouterr()

    sink: list[str] = []
    asm.set_role("test-role", warnings_sink=sink)

    assert any("taken over" in w for w in sink)
    assert "taken over" not in capsys.readouterr().out


# ----- retiring the pre-1337 layout (R7) -----


def test_pre_1337_copies_and_manifest_are_retired(project_with_hook_skill):
    project, lib = project_with_hook_skill
    githooks = project / GITHOOKS_DIR
    event_d = githooks / "pre-commit.d"
    event_d.mkdir(parents=True)
    copy = event_d / "hook_skill-check.sh"
    copy.write_text("#!/usr/bin/env bash\nexit 0\n")
    copy.chmod(0o755)
    (githooks / GITHOOKS_BYPASS_JOURNAL).write_text("# ai-hats managed — do not edit\n")
    (githooks / GITHOOKS_MANIFEST).write_text(
        "# ai-hats-owner: git-hooks\n"
        "aaaaaaaaaaaa  pre-commit.d/hook_skill-check.sh\n"
        f"bbbbbbbbbbbb  {GITHOOKS_BYPASS_JOURNAL}\n"
    )

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    assert not copy.exists()
    assert not (githooks / GITHOOKS_MANIFEST).exists()
    assert not (githooks / GITHOOKS_BYPASS_JOURNAL).exists()
    assert not event_d.exists(), "an emptied <event>.d/ is removed too"
    assert (githooks / "pre-commit").is_file()


def test_retirement_keeps_a_script_the_user_dropped_in(project_with_hook_skill):
    """R8: cleanup is driven by the old manifest, which is exactly what tells an
    ai-hats copy apart from somebody's own script in the same directory."""
    project, lib = project_with_hook_skill
    githooks = project / GITHOOKS_DIR
    event_d = githooks / "pre-commit.d"
    event_d.mkdir(parents=True)
    ours = event_d / "hook_skill-check.sh"
    ours.write_text("#!/usr/bin/env bash\nexit 0\n")
    theirs = event_d / "zz-user-custom.sh"
    theirs.write_text("#!/usr/bin/env bash\necho mine\n")
    theirs.chmod(0o755)
    (githooks / GITHOOKS_MANIFEST).write_text(
        "# ai-hats-owner: git-hooks\naaaaaaaaaaaa  pre-commit.d/hook_skill-check.sh\n"
    )

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    assert not ours.exists()
    assert theirs.is_file(), "a script ai-hats never wrote is not ai-hats's to delete"
    assert theirs.read_text() == "#!/usr/bin/env bash\necho mine\n"


# ----- HATS-617: skill-lint-gate is scoped to skill-authoring roles -----

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LIBRARY = _REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


def _composed_skill_names(role: str) -> set[str]:
    """Skills resolved for `role` against THIS checkout's library."""
    asm = Assembler(_REPO_ROOT, library_paths=[_LIBRARY / "core", _LIBRARY / "usage"])
    result = asm.composer.compose(role, overlay=asm._get_overlay(role))
    assert result.errors == [], result.errors
    return {s.name for s in result.skills}


def test_skill_lint_gate_present_for_skill_authoring_roles():
    for role in ("maintainer", "role-curator"):
        names = _composed_skill_names(role)
        assert "skill-lint-gate" in names, f"{role} missing skill-lint-gate"
        # Positive control: a False above means "absent", not "compose failed".
        assert "skill-template" in names, f"{role} pos-control skill-template missing"


def test_skill_lint_gate_absent_from_non_authoring_roles():
    for role in ("assistant", "architect"):
        assert "skill-lint-gate" not in _composed_skill_names(role), (
            f"{role} unexpectedly received skill-lint-gate"
        )


# ----- a lossy composition may not justify ABSENCE -----


def _break_the_role(lib: Path) -> None:
    """Point the role at a trait that does not exist — the whole subtree, and
    with it the hook-declaring skill, drops out of the composition."""
    (lib / "roles" / "test-role" / "config.yaml").write_text(
        "name: test-role\n"
        "priorities: [Quality]\n"
        "composition:\n"
        "  traits:\n"
        "    - trait-typo\n"
        "injection: Role.\n"
    )


def test_a_lossy_composition_does_not_uninstall_the_dispatcher(composed):
    """On master a trait typo made `set_role` compose to zero skills, and
    `install_git_hooks` read that as "no event is declared any more" and
    deleted every managed dispatcher — silently, rc 0, on every session start.
    """
    project, lib, _asm = composed
    dispatcher = project / GITHOOKS_DIR / "pre-commit"
    assert dispatcher.is_file(), "positive control: the gate was installed first"

    _break_the_role(lib)
    warnings: list[str] = []
    asm = Assembler(project, library_paths=[lib])
    result = compose_to_report(asm, "test-role")
    assert result.lost, "the fixture must actually produce a lossy composition"
    install_git_hooks(project, result, warnings_sink=warnings)

    assert dispatcher.is_file(), "a lost gate is not an absent gate"
    assert any("composed with losses" in w for w in warnings), (
        f"the refusal to drop must be audible, got: {warnings}"
    )


def test_a_clean_composition_still_drops_a_retired_dispatcher(composed):
    """The positive control for the test above. Without it, "the dispatcher
    survived" would be indistinguishable from "the drop broke entirely" — and
    a dispatcher nothing declares any more must still be retired (HATS-1337).
    """
    project, lib, _asm = composed
    dispatcher = project / GITHOOKS_DIR / "pre-commit"
    assert dispatcher.is_file()

    # A role that HONESTLY declares no git hooks: it composes cleanly, so the
    # empty `wanted` set means what it says.
    (lib / "traits" / "trait-base" / "config.yaml").write_text(
        "name: trait-base\ncomposition: {}\ninjection: Base.\n"
    )
    asm = Assembler(project, library_paths=[lib])
    result = compose_to_report(asm, "test-role")
    assert not result.lost, "this arm must be clean — otherwise it proves nothing"
    install_git_hooks(project, result)

    assert not dispatcher.exists(), "a gate nobody declares any more is retired"
