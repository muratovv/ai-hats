"""e2e (HATS-1337)

flow:   a developer committing code across git worktrees or after framework updates
        when ai-hats environment variables may be missing
cmds:
    # in a project with missing ai-hats binary or inside a linked worktree
    git commit -m "feature"
expect: commits succeed gracefully when ai-hats binaries are unreachable and linked
        worktrees execute the same gate suite as the main checkout
why:    git hooks must fail open to avoid wedging developer commits when tools are
        unreachable while ensuring linked worktrees enforce consistent quality gates
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AI_HATS_PYTHON = Path(sys.executable)
AI_HATS_CMD = (str(AI_HATS_PYTHON), "-m", "ai_hats")

pytestmark = pytest.mark.integration

GUARD = """#!/usr/bin/env bash
set -uo pipefail
echo "GUARD ${AI_HATS_HOOK_EVENT} v${GUARD_VERSION:-1}" >&2
touch "$(git rev-parse --show-toplevel)/.marker-guard"
exit 0
"""


def _pinned_env() -> dict[str, str]:
    """Env pinning ai-hats to THIS checkout, GIT_* stripped (HATS-887)."""
    from _helpers.env import checkout_pythonpath

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env[ENV_AI_HATS_VENV] = str(AI_HATS_PYTHON.parent.parent)
    return env


def _git(*args: str, cwd: Path, env: dict[str, str] | None = None):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        env=env or _pinned_env(),
    )


def _git_ok(*args: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    cp = _git(*args, cwd=cwd, env=env)
    assert cp.returncode == 0, f"git {' '.join(args)}:\n{cp.stdout}\n{cp.stderr}"
    return cp.stdout.strip()


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    _git_ok("-c", "init.defaultBranch=master", "init", "--quiet", cwd=project)
    _git_ok("config", "user.email", "t@e.x", cwd=project)
    _git_ok("config", "user.name", "t", cwd=project)

    lib = tmp_path / "lib"
    skill = lib / "skills" / "guard_skill"
    (skill / "git_hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: guard_skill\ndescription: a pre-commit guard\n"
        "ai_hats:\n  git_hooks:\n    pre-commit:\n      - git_hooks/guard.sh\n---\n\n# G\n"
    )
    guard = skill / "git_hooks" / "guard.sh"
    guard.write_text(GUARD)
    guard.chmod(0o755)

    (lib / "traits" / "trait-base").mkdir(parents=True)
    (lib / "traits" / "trait-base" / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - guard_skill\ninjection: B.\n"
    )
    (lib / "roles" / "guard-role").mkdir(parents=True)
    (lib / "roles" / "guard-role" / "config.yaml").write_text(
        "name: guard-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / PROJECT_CONFIG).write_text(f"provider: claude\nlibrary_paths:\n  - {lib}\n")
    return project, lib


def _self_init(project: Path) -> None:
    cp = subprocess.run(
        [*AI_HATS_CMD, "self", "init", "-p", "claude", "-r", "guard-role", "--no-wizard"],
        cwd=str(project),
        env=_pinned_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert cp.returncode == 0, f"self init failed:\n{cp.stdout}\n{cp.stderr}"
    assert (project / ".githooks" / "pre-commit").is_file(), "dispatcher not installed"


def _commit(
    project: Path, name: str, *, env: dict[str, str] | None = None, cwd: Path | None = None
):
    where = cwd or project
    (where / name).write_text("x\n")
    _git_ok("add", name, cwd=where, env=env)
    return _git("commit", "-m", name, "--quiet", cwd=where, env=env)


def test_a_commit_lands_when_ai_hats_is_gone(tmp_path: Path):
    """R2: fail-open. The old dispatcher refused the event and prescribed
    `ai-hats self init` — the very binary that is missing."""
    project, _lib = _project(tmp_path)
    _self_init(project)

    # ai-hats fully unavailable to the hook: no pinned venv, no importable package.
    stripped = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AI_HATS_"))}
    stripped["PYTHONPATH"] = str(tmp_path / "nothing")
    stripped["PATH"] = "/usr/bin:/bin"

    cp = _commit(project, "a.txt", env=stripped)

    assert cp.returncode == 0, f"a human commit must never be wedged:\n{cp.stderr}"
    assert "fail-open" in cp.stderr, cp.stderr
    assert not (project / ".marker-guard").exists(), "the gate cannot have run"


def test_a_worktree_commit_runs_the_same_gates(tmp_path: Path):
    """R1 / Z1: `.githooks/` is generated and gitignored, so a relative
    `core.hooksPath` resolved to nothing inside a linked worktree and every gate
    was silently off there. Absolute closes it."""
    project, _lib = _project(tmp_path)
    _self_init(project)
    _commit(project, "seed.txt")

    worktree = tmp_path / "wt"
    _git_ok("worktree", "add", "-q", "-b", "task/x", str(worktree), cwd=project)
    assert not (worktree / ".githooks").exists(), "the fixture must not fake a copy"

    cp = _commit(project, "inwt.txt", cwd=worktree)

    assert cp.returncode == 0, f"{cp.stdout}\n{cp.stderr}"
    assert "GUARD pre-commit" in cp.stderr, (
        f"no gate ran inside the worktree — Z1 regression\n{cp.stderr}"
    )
    assert (worktree / ".marker-guard").exists()


def test_the_next_commit_after_an_update_runs_the_new_gates(tmp_path: Path):
    """R9 / M12: the dispatcher is not bound to a version, so a `self update`
    needs no re-materialization step before the gates change."""
    project, lib = _project(tmp_path)
    _self_init(project)
    _commit(project, "before.txt")
    assert "GUARD pre-commit v1" in _commit(project, "v1.txt").stderr

    # The library ships a new gate version. Nothing re-runs `self init`.
    guard = lib / "skills" / "guard_skill" / "git_hooks" / "guard.sh"
    guard.write_text(GUARD.replace("v${GUARD_VERSION:-1}", "v2"))
    guard.chmod(0o755)
    dispatcher_before = (project / ".githooks" / "pre-commit").read_bytes()

    cp = _commit(project, "after.txt")

    assert cp.returncode == 0, cp.stderr
    assert "GUARD pre-commit v2" in cp.stderr, (
        f"the commit ran the OLD gate — the orchestrator is version-bound\n{cp.stderr}"
    )
    assert (project / ".githooks" / "pre-commit").read_bytes() == dispatcher_before, (
        "the durable artifact must survive by content, not be re-materialized"
    )


# ---------------------------------------------------------------------------
# HATS-1597 / HATS-1828: a gate that cannot be exec'd never raises — and since
# HATS-1828 it refuses rather than waving the commit through
# ---------------------------------------------------------------------------


def _assert_refuses_with_a_hatch(cp, project: Path, why: str) -> None:
    """The contract, spelled once. A declared gate that cannot run is ai-hats'
    own delivery failure, not a verdict — so the commit stops. `Traceback` is
    still checked explicitly (HATS-1597): it is the original reported symptom,
    and an exit code alone would not tell a refusal from a crash."""
    assert "Traceback" not in cp.stderr, f"{why}: a hook raised at the human\n{cp.stderr}"
    assert cp.returncode != 0, f"{why}: an undeliverable gate must not pass\n{cp.stderr}"
    assert "guard.sh" in cp.stderr, f"{why}: the broken gate is not named\n{cp.stderr}"
    assert "AI_HATS_GIT_GATE_BROKEN_ACK" in cp.stderr, f"{why}: no hatch named\n{cp.stderr}"
    assert not (project / ".marker-guard").exists(), f"{why}: the gate cannot have run"


def test_a_commit_stops_when_a_gate_is_not_executable(tmp_path: Path):
    """`git_hooks:` is a public extension point, so a declared gate arrives with
    whatever mode its author committed. Without the exec bit the dispatcher hit
    `PermissionError` inside `subprocess.run` and the commit died on a traceback.

    HATS-1597 answered that with a skip, because `--no-verify` — which disarms
    the WHOLE chain — was the only alternative. HATS-1828 supplies a narrower
    one, so the gate can now refuse and name it instead of disarming itself.
    """
    project, lib = _project(tmp_path)
    _self_init(project)  # init first: only the commit path is under test
    (lib / "skills" / "guard_skill" / "git_hooks" / "guard.sh").chmod(0o644)

    _assert_refuses_with_a_hatch(_commit(project, "a.txt"), project, "non-executable gate")


def test_the_hatch_lands_the_commit_the_broken_gate_stopped(tmp_path: Path):
    """The refusal above is only legitimate because this flag works.

    Also the bootstrap case: the commit that FIXES a broken gate would otherwise
    be blocked by the very gate it repairs.
    """
    project, lib = _project(tmp_path)
    _self_init(project)
    (lib / "skills" / "guard_skill" / "git_hooks" / "guard.sh").chmod(0o644)

    cp = _commit(project, "a.txt", env={**_pinned_env(), "AI_HATS_GIT_GATE_BROKEN_ACK": "1"})

    assert cp.returncode == 0, f"the named hatch must open\n{cp.stderr}"
    assert not (project / ".marker-guard").exists(), "the broken gate cannot have run"


def test_a_commit_stops_when_a_gate_has_no_shebang(tmp_path: Path):
    """Its own test, not a parametrize case: this one is exec-bit CLEAN, so an
    `os.access(X_OK)` check passes it and the kernel refuses at execve instead
    (`OSError: [Errno 8] Exec format error`). A fix for the mode alone leaves it red.
    """
    project, lib = _project(tmp_path)
    _self_init(project)
    guard = lib / "skills" / "guard_skill" / "git_hooks" / "guard.sh"
    guard.write_text(GUARD.split("\n", 1)[1])  # every line but the shebang
    guard.chmod(0o755)

    _assert_refuses_with_a_hatch(_commit(project, "a.txt"), project, "gate without a shebang")


# ---------------------------------------------------------------------------
# HATS-1828: the chain is bounded, end to end through a real `git commit`
# ---------------------------------------------------------------------------


def _bounded_env(seconds: str = "1") -> dict[str, str]:
    return {**_pinned_env(), "AI_HATS_GIT_HOOK_TIMEOUT_S": seconds}


def test_a_hung_gate_stops_the_commit_instead_of_hanging_it(tmp_path: Path):
    """Before this, `run_chain` spawned with no timeout at all — a gate that hung
    hung `git commit` with nothing but Ctrl-C to end it, and no Ctrl-C in CI, in
    cron, or in an agent session.

    The bound is the point; stopping is the consequence. A hang is the script's
    own behaviour, so the refusal points at the budget, not at the skip flag.
    """
    project, lib = _project(tmp_path)
    _self_init(project)
    guard = lib / "skills" / "guard_skill" / "git_hooks" / "guard.sh"
    guard.write_text("#!/usr/bin/env bash\nsleep 60\n")
    guard.chmod(0o755)

    cp = _commit(project, "a.txt", env=_bounded_env())

    assert cp.returncode != 0, f"a hung gate must not pass the commit\n{cp.stderr}"
    assert "AI_HATS_GIT_HOOK_TIMEOUT_S" in cp.stderr, f"no budget named\n{cp.stderr}"


def test_a_hung_dropin_is_bounded_too(tmp_path: Path):
    """A drop-in is a human's own script under `<event>.d/`, and it never passes
    through `resolve_git_gates` — it was the headline case for this card, since
    nothing about it is ai-hats' to validate ahead of time."""
    project, _lib = _project(tmp_path)
    _self_init(project)
    dropin = project / ".githooks" / "pre-commit.d" / "zz-hang.sh"
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text("#!/usr/bin/env bash\nsleep 60\n")
    dropin.chmod(0o755)

    cp = _commit(project, "a.txt", env=_bounded_env())

    assert cp.returncode != 0, f"a hung drop-in must not pass the commit\n{cp.stderr}"
    assert "AI_HATS_GIT_HOOK_TIMEOUT_S" in cp.stderr, f"no budget named\n{cp.stderr}"


def test_the_broken_gate_hatch_does_not_open_a_hung_one(tmp_path: Path):
    """The two hatches are not interchangeable, and saying so is the contract.

    `AI_HATS_GIT_GATE_BROKEN_ACK` covers gates ai-hats failed to DELIVER. A gate
    that ran and hung was delivered fine; letting the delivery flag wave it
    through would quietly turn a permanently-hanging gate into a disarmed one.
    """
    project, lib = _project(tmp_path)
    _self_init(project)
    guard = lib / "skills" / "guard_skill" / "git_hooks" / "guard.sh"
    guard.write_text("#!/usr/bin/env bash\nsleep 60\n")
    guard.chmod(0o755)

    cp = _commit(project, "a.txt", env={**_bounded_env(), "AI_HATS_GIT_GATE_BROKEN_ACK": "1"})

    assert cp.returncode != 0, f"the delivery hatch must not open a hang\n{cp.stderr}"


def test_a_broken_role_does_not_disarm_the_committed_gates(tmp_path: Path):
    """A lossy composition may not disarm a gate — end to end, through a real
    `git commit`.

    On master, pointing the role at a missing trait composed to zero skills;
    the gate then vanished from `.githooks/` on the next install, and every
    later commit sailed through green. Both halves are asserted here: the
    commit must be REFUSED, and the dispatcher must still be on disk.

    The positive control is `test_a_worktree_commit_runs_the_same_gates`
    above — it proves this harness installs and fires gates at all.
    """
    project, lib = _project(tmp_path)
    _self_init(project)
    dispatcher = project / ".githooks" / "pre-commit"

    assert _commit(project, "before").returncode == 0, "control: a clean role commits"
    assert (project / ".marker-guard").exists(), "control: the gate actually ran"

    (lib / "roles" / "guard-role" / "config.yaml").write_text(
        "name: guard-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-typo\ninjection: R.\n"
    )

    cp = _commit(project, "after")
    assert cp.returncode != 0, (
        f"a commit whose gates cannot be composed must be refused, got rc=0:\n"
        f"{cp.stdout}\n{cp.stderr}"
    )
    assert "trait-typo" in (cp.stdout + cp.stderr), (
        f"the refusal must NAME what went missing:\n{cp.stdout}\n{cp.stderr}"
    )
    assert dispatcher.is_file(), "the dispatcher must survive a composition it cannot trust"
