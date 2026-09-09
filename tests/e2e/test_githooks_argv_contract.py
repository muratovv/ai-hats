"""e2e (HATS-1519)

flow:   a developer executing a git commit in a project where installed git hooks
        and CLI versions differ
cmds:
    git commit -m "update"
expect: the commit succeeds when argument separators match and skips with a fail-open
        warning when unknown flags are passed
why:    installed hook stubs and venv CLI packages are versioned independently, so
        version skew must degrade to a skip rather than blocking commits
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

pytestmark = [pytest.mark.integration, pytest.mark.guards]

#: Records the argv git handed the hook, NUL-separated so a path with spaces
#: survives the round trip.
GATE = """#!/usr/bin/env bash
set -uo pipefail
printf '%s\\0' "$@" > "$(git rev-parse --show-toplevel)/.gate-argv"
exit {exit_code}
"""


def _pinned_env() -> dict[str, str]:
    """Env pinning ai-hats to THIS checkout, GIT_* stripped (HATS-887)."""
    from _helpers.env import checkout_pythonpath

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env[ENV_AI_HATS_VENV] = str(AI_HATS_PYTHON.parent.parent)
    return env


def _project(tmp_path: Path, *, gate_exit: int = 0) -> Path:
    """A real git project composing one `commit-msg` gate, hooks installed."""
    from _helpers.git import init_repo

    project = tmp_path / "project"
    project.mkdir()
    # harden=False: the hardening pins `core.hooksPath` to /dev/null, which would
    # switch off the very dispatcher under test.
    init_repo(project, branch="master", harden=False)

    lib = tmp_path / "lib"
    skill = lib / "skills" / "argv_skill"
    (skill / "git_hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: argv_skill\ndescription: records the hook argv\n"
        "ai_hats:\n  git_hooks:\n    commit-msg:\n      - git_hooks/argv.sh\n---\n\n# A\n"
    )
    gate = skill / "git_hooks" / "argv.sh"
    gate.write_text(GATE.format(exit_code=gate_exit))
    gate.chmod(0o755)

    (lib / "traits" / "trait-base").mkdir(parents=True)
    (lib / "traits" / "trait-base" / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - argv_skill\ninjection: B.\n"
    )
    (lib / "roles" / "argv-role").mkdir(parents=True)
    (lib / "roles" / "argv-role" / "config.yaml").write_text(
        "name: argv-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / PROJECT_CONFIG).write_text(f"provider: claude\nlibrary_paths:\n  - {lib}\n")

    cp = subprocess.run(
        [
            str(AI_HATS_PYTHON),
            "-m",
            "ai_hats",
            "self",
            "init",
            "-p",
            "claude",
            "-r",
            "argv-role",
            "--no-wizard",
        ],
        cwd=str(project),
        env=_pinned_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert cp.returncode == 0, f"self init failed:\n{cp.stdout}\n{cp.stderr}"
    assert (project / ".githooks" / "commit-msg").is_file(), "dispatcher not installed"
    return project


def _commit(
    project: Path, name: str, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Stage and commit one file, returning the commit's own outcome.

    Not `_helpers.git.git`: this is the call under test, so it needs the pinned
    env the hook resolves ai-hats through, and it must survive a non-zero exit —
    a refused commit is a result here, not an error.
    """
    from _helpers.git import git

    (project / name).write_text("x\n")
    git(project, "add", name)
    return subprocess.run(
        ["git", "commit", "-m", name, "--quiet"],
        cwd=str(project),
        capture_output=True,
        text=True,
        timeout=120,
        env=env or _pinned_env(),
    )


def _gate_argv(project: Path) -> list[str]:
    return (project / ".gate-argv").read_text().split("\0")[:-1]


def _skew_the_stub(project: Path, flag: str) -> None:
    """Make the installed stub pass a flag this CLI never learned.

    A faithful stand-in for the field skew: the stub is frozen per project while
    the CLI ships with the venv, so a stub from a later ai-hats hands arguments
    an older entry point cannot parse. Editing the installed file is the only way
    to stage that without shipping a second stub — the substring is asserted so a
    template edit turns this into a red test, never a silent no-op.
    """
    stub = project / ".githooks" / "commit-msg"
    text = stub.read_text()
    anchor = '--githooks-dir "$GITHOOKS_DIR" --'
    assert anchor in text, f"the stub no longer builds argv as expected:\n{text}"
    stub.write_text(text.replace(anchor, f'--githooks-dir "$GITHOOKS_DIR" {flag} --'))


def test_gits_argument_reaches_the_gate_across_the_stubs_separator(tmp_path: Path):
    """R1/R2: the `--` the stub inserts must not cost the commit its argument.

    argparse honours that separator only on 3.13+; on 3.11/3.12 it surfaces as
    `unrecognized arguments: --`, and argparse's SystemExit(2) — raised inside
    `main`, past the stub's import guard — becomes the hook's verdict. Every
    commit in the project refused, which is how HATS-1519 was found.
    """
    project = _project(tmp_path)

    cp = _commit(project, "a.txt")

    assert cp.returncode == 0, f"the dispatcher refused a plain commit:\n{cp.stderr}"
    argv = _gate_argv(project)
    assert len(argv) == 1, f"the gate saw {argv}, not git's single argument"
    assert argv[0].endswith("COMMIT_EDITMSG"), argv


def test_a_stub_passing_an_unknown_flag_refuses_and_names_its_hatch(tmp_path: Path):
    """Stub/CLI skew: the separator was one shape, a flag a later stub learns to
    pass is the next, and both reach argparse as SystemExit(2).

    HATS-1519 made this a skip, reasoning that fail-open is the stub's declared
    purpose so the entry point owes it the same. HATS-1828 retires that
    inheritance: the stub fails open because its bytes CANNOT be repaired at
    commit time, and this entry point can — it ships with this ai-hats, and
    `self update` fixes it. Gates going quiet on skew is the failure, not the fix.
    """
    project = _project(tmp_path)
    _skew_the_stub(project, "--hook-stdin-mode replay")

    cp = _commit(project, "b.txt")

    assert cp.returncode != 0, f"skew must not silently disarm the gates:\n{cp.stderr}"
    assert "self update" in cp.stderr, cp.stderr
    assert "AI_HATS_GIT_GATE_BROKEN_ACK" in cp.stderr, "a deny must name its hatch"
    assert not (project / ".gate-argv").exists(), "no gate may run when the dispatcher gave up"


def test_the_hatch_lands_a_commit_the_skew_stopped(tmp_path: Path):
    """The refusal above is only legitimate because this flag works — and here it
    cannot be journalled (no project is parsed), so the message must say so."""
    project = _project(tmp_path)
    _skew_the_stub(project, "--hook-stdin-mode replay")

    cp = _commit(project, "b.txt", env={**_pinned_env(), "AI_HATS_GIT_GATE_BROKEN_ACK": "1"})

    assert cp.returncode == 0, f"the named hatch must open:\n{cp.stderr}"
    assert "NOT RECORDED" in cp.stderr, f"an unrecordable skip must say so:\n{cp.stderr}"


def test_a_refusing_gate_still_blocks_the_commit(tmp_path: Path):
    """R4: the hatch is scoped to OUR parse, never to a gate's verdict.

    Caught around `main` instead of around `parse_args`, the same fail-open would
    turn every refusal into a silent pass — gates installed, gates ignored, which
    is worse than the bug it fixes.
    """
    project = _project(tmp_path, gate_exit=1)

    cp = _commit(project, "c.txt")

    assert cp.returncode != 0, "a gate that refused must still block the commit"
    assert _gate_argv(project), "the gate must actually have run"
