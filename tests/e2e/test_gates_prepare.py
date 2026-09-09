"""e2e (HATS-1664)

flow:   the gate primitive making a scratch checkout of a merge commit runnable
cmds:
    bash scripts/gates.sh --prepare
expect: the dispatcher delegates to the worktree-venv hook of the tree it is
        preparing, keeps an already-usable venv, and installs the tree's current
        pins into it every time
why:    a checkout minted by `git worktree add` has no .venv, so every
        real-subprocess stage would exercise the MAIN checkout's installed code
        while claiming to judge the commit; and a venv that merely EXISTS can
        still hold the pins of a tree that has since been rebased (HATS-1939)
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.git import git, init_repo

pytestmark = [pytest.mark.integration, pytest.mark.gates]

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


#: Records argv per call, and materializes bin/python on `uv venv` so the
#: hook's own readiness probe sees a real venv afterwards.
_UV_STUB = """#!/usr/bin/env bash
echo "$@" >> "{calls}"
words=()
for arg in "$@"; do [[ "$arg" == -* ]] || words+=("$arg"); done
if [[ "${{words[0]:-}}" == "venv" ]]; then
    target="${{words[1]:-.venv}}"
    mkdir -p "$target/bin"
    printf '#!/bin/sh\\nexit 0\\n' > "$target/bin/python"
    chmod +x "$target/bin/python"
fi
exit 0
"""


def _stub_uv(path_dir: Path, calls: Path) -> Path:
    path_dir.mkdir(parents=True, exist_ok=True)
    uv = path_dir / "uv"
    uv.write_text(_UV_STUB.format(calls=calls), encoding="utf-8")
    uv.chmod(0o755)
    return path_dir


def _prepare(root: Path, *, path_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if path_dir is not None:
        env["PATH"] = f"{path_dir}{os.pathsep}/usr/bin:/bin"
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", "scripts/gates.sh", "--prepare"],
        cwd=root,
        capture_output=True,
        text=True,
        env=env,
    )


def test_preparing_keeps_a_usable_venv_but_still_installs_the_pins(checkout: Path, tmp_path: Path):
    """Idempotent, because `gate_run_and_stamp_rev` calls it on every rev road
    (HATS-1664) — including a checkout that arrived already provisioned.

    Until HATS-1939 this asserted that a usable venv made `--prepare` a no-op.
    That is what let a rebased worktree judge a commit with the previous tree's
    dependencies installed, so the no-op half is gone: the venv is still KEPT
    (never rebuilt), and the tree's pins go into it every time.
    """
    venv = _usable_venv(checkout)
    marker = venv / "kept-across-prepare"
    marker.write_text("a usable venv is reused, not rebuilt\n", encoding="utf-8")
    calls = tmp_path / "uv-calls.txt"

    ran = _prepare(checkout, path_dir=_stub_uv(tmp_path / "bin", calls))

    assert ran.returncode == 0, ran.stdout + ran.stderr
    recorded = calls.read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith("venv") for line in recorded), (
        f"a usable venv was rebuilt from scratch: {recorded}"
    )
    assert any(line.startswith("pip install") for line in recorded), (
        f"--prepare skipped the install and left the pins unjudged: {recorded}"
    )
    assert marker.exists(), "a usable venv must survive being asked about"


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


# --- HATS-1939: the venv must match the tree's pins, not merely exist ---------

#: A root project whose one dependency is a sibling path, so a "pin moved on
#: master" is reproducible with no index and no network.
_ROOT_PYPROJECT = """\
[project]
name = "wtroot"
version = "0.1.0"
dependencies = ["wtdep"]

[project.optional-dependencies]
dev = []

[tool.uv.sources]
wtdep = { path = "dep" }

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["wtroot"]
"""

_DEP_PYPROJECT = """\
[project]
name = "wtdep"
version = "{version}"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["wtdep"]
"""


def _installed_version(venv: Path, dist: str) -> str | None:
    """The version recorded in site-packages, read from the METADATA on disk."""
    for meta in venv.glob(f"lib/python*/site-packages/{dist}-*.dist-info/METADATA"):
        for line in meta.read_text(encoding="utf-8").splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    return None


@pytest.fixture()
def pinned_checkout(tmp_path: Path) -> Path:
    """A real, installable checkout carrying the dispatcher and the hook."""
    root = tmp_path / "pinned"
    (root / "scripts").mkdir(parents=True)
    init_repo(root)
    shutil.copy(DISPATCHER, root / "scripts" / "gates.sh")
    (root / HOOK_REL).parent.mkdir(parents=True)
    shutil.copy(REPO_ROOT / HOOK_REL, root / HOOK_REL)
    (root / "pyproject.toml").write_text(_ROOT_PYPROJECT, encoding="utf-8")
    (root / "wtroot").mkdir()
    (root / "wtroot" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "dep" / "wtdep").mkdir(parents=True)
    (root / "dep" / "wtdep" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "dep" / "pyproject.toml").write_text(
        _DEP_PYPROJECT.format(version="2.0.0"), encoding="utf-8"
    )
    git(root, "add", "-A")
    git(root, "commit", "-m", "a checkout whose pins can move")
    return root


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is what installs the pins")
def test_prepare_brings_a_usable_venv_up_to_the_trees_current_pins(pinned_checkout: Path):
    """A worktree that outlives a rebase carries a venv installed from the OLD
    pins. The venv is structurally perfect — the HATS-1339 probe passes — so the
    hook's fast path called it "already usable" and the drift survived. It then
    surfaced from whatever imported the moved package first, reading as a defect
    in that code (HATS-1862: `No module named 'mcp.server.fastmcp'`).
    """
    venv = pinned_checkout / ".venv"

    first = _prepare(pinned_checkout)
    assert first.returncode == 0, first.stdout + first.stderr
    assert _installed_version(venv, "wtdep") == "2.0.0", "the venv was not provisioned"

    # master moves under the worktree: the tree now asks for a different version.
    (pinned_checkout / "dep" / "pyproject.toml").write_text(
        _DEP_PYPROJECT.format(version="1.0.0"), encoding="utf-8"
    )

    second = _prepare(pinned_checkout)

    assert second.returncode == 0, second.stdout + second.stderr
    assert _installed_version(venv, "wtdep") == "1.0.0", (
        "--prepare left the venv on the pin the tree no longer declares"
    )
