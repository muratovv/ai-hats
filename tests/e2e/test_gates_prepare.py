"""e2e (HATS-1664)

flow:   the gate primitive making a scratch checkout of a merge commit runnable
cmds:
    bash scripts/gates.sh --prepare
expect: the dispatcher delegates to the worktree-venv hook of the tree it is
        preparing, and leaves an already-usable venv untouched
why:    a checkout minted by `git worktree add` has no .venv, so every
        real-subprocess stage would exercise the MAIN checkout's installed code
        while claiming to judge the commit
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.git import git, init_repo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
DISPATCHER = REPO_ROOT / "scripts" / "gates.sh"
HOOK_REL = Path(
    "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/worktree-venv/hooks/provision-venv.sh"
)


def _usable_venv(root: Path) -> Path:
    """The shape provision-venv.sh probes for: an executable interpreter, a
    pyvenv.cfg, and a RECORD proving the installed files survived (HATS-1339)."""
    venv = root / ".venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    (venv / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    record = venv / "lib" / "python3.11" / "site-packages" / "thing-1.0.dist-info"
    record.mkdir(parents=True)
    (record / "RECORD").write_text("thing/__init__.py,,\n", encoding="utf-8")
    return venv


@pytest.fixture()
def checkout(tmp_path: Path) -> Path:
    """A repository carrying this repo's real dispatcher and provisioning hook."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    init_repo(root)
    shutil.copy(DISPATCHER, root / "scripts" / "gates.sh")
    (root / HOOK_REL).parent.mkdir(parents=True)
    shutil.copy(REPO_ROOT / HOOK_REL, root / HOOK_REL)
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "a checkout worth preparing")
    return root


def _prepare(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", "scripts/gates.sh", "--prepare"],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_preparing_a_checkout_that_already_has_a_venv_changes_nothing(checkout: Path):
    """Idempotent, because `gate_run_and_stamp_rev` calls it on every rev road
    (HATS-1664) — including a checkout that arrived already provisioned."""
    venv = _usable_venv(checkout)

    ran = _prepare(checkout)

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "already usable" in ran.stdout + ran.stderr
    assert (venv / "bin" / "python").exists(), "a usable venv must survive being asked about"


def test_preparing_reaches_the_hook_belonging_to_the_tree_it_prepares(checkout: Path):
    """Not the installed library's copy: an unprepared checkout has no
    interpreter that could import one, and the hook that runs must be the one
    the judged content carries."""
    (checkout / HOOK_REL).write_text(
        '#!/usr/bin/env bash\necho "THIS TREE\'S HOOK saw $AI_HATS_WORKTREE_PATH"\n',
        encoding="utf-8",
    )

    ran = _prepare(checkout)

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "THIS TREE'S HOOK" in ran.stdout + ran.stderr
    assert str(checkout.resolve()) in ran.stdout + ran.stderr, (
        "and it is told which checkout to provision"
    )


def test_a_checkout_without_the_hook_says_so_instead_of_going_quiet(checkout: Path):
    """A silent no-op here would be the expensive kind: the stages would run
    against another checkout's code and report green (dev_rule_silent_fallback)."""
    (checkout / HOOK_REL).unlink()

    ran = _prepare(checkout)

    assert ran.returncode != 0, "nothing was prepared, and the caller must be able to tell"
    assert "nothing to prepare" in ran.stdout + ran.stderr
