"""E2E: ``self init`` survives updating itself to a different tree (HATS-1126).

Value under test: init's embedded update reinstalls the very package the running
interpreter executes from. Continuing in-process leaves modules already in
``sys.modules`` on the OLD tree while anything imported later is read from the
NEW one — a split module set. HATS-1115 hit it as
``ImportError: cannot import name 'PROVIDER_GEMINI' from 'ai_hats.constants'``,
raised from ``_assembler()``, naming neither the update nor the version change.

Setup (real launcher + real uv install, per ``dev_rule_e2e_gate`` — no stubs):

  - Own function-scoped launcher venv (NOT the session-shared one: this test
    reinstalls ai-hats inside it), built from the repo as it stands = tree Y.
  - Tree X: a copy of the repo where ``constants.py`` gains a probe symbol and
    ``assembler.py`` imports it at module level. Both trees are internally
    consistent; only the pair is incompatible — exactly the incident's shape,
    where the resident module lacks what the later-imported sibling needs.
  - ``AI_HATS_REPO_URL`` points at X, so init's embedded update swaps Y for X
    mid-run. A PTY on stdin keeps init on the wizard path that runs the update
    (same technique as test_init_verifies_install_before_success.py).
  - A stub ``ai-hats`` earlier on PATH so the wizard hand-off at the end of init
    exits instead of launching a provider session.

Assertion: init finishes without the split-module ImportError.

Fail-under-revert: drop the ``os.execv`` in ``cli/assembly.py`` and the run
raises ``cannot import name 'PROBE_SYMBOL' from 'ai_hats.constants'`` — the
HATS-1115 signature.
"""

from __future__ import annotations

import os
import pty
import shutil
import subprocess
from pathlib import Path

import pytest
from ai_hats.constants import ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV

pytestmark = pytest.mark.install_heavy  # own launcher venv + two real uv installs

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PROBE = "PROBE_SYMBOL"
_SPLIT_SIGNATURE = f"cannot import name '{_PROBE}'"


def _divergent_source(dst: Path) -> Path:
    """A repo copy whose assembler imports a constant the installed tree lacks."""
    src = dst / "tree-x"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(REPO_ROOT), str(src)],
        check=True, capture_output=True, text=True,
    )
    # Overlay the working tree so a dirty checkout is tested, not the last commit.
    shutil.copytree(REPO_ROOT / "src", src / "src", dirs_exist_ok=True)

    constants = src / "src" / "ai_hats" / "constants.py"
    constants.write_text(constants.read_text() + f'\n{_PROBE} = "x"\n')

    assembler = src / "src" / "ai_hats" / "assembler.py"
    text = assembler.read_text()
    anchor = "from .constants import ("
    assert anchor in text, "assembler.py no longer imports from .constants"
    assembler.write_text(text.replace(anchor, f"{anchor}\n    {_PROBE},", 1))
    return src


def _stub_ai_hats_on_path(dst: Path) -> Path:
    """A no-op `ai-hats` so init's wizard hand-off exits instead of spawning a session."""
    bin_dir = dst / "stub-bin"
    bin_dir.mkdir()
    stub = bin_dir / "ai-hats"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(0o755)
    return bin_dir


def test_init_completes_when_the_update_swaps_the_running_tree(
    tmp_path: Path, repo_root: Path
):
    """The embedded update replaces this interpreter's own package — init must not split."""
    from _helpers.project import pin_edge_channel
    from _helpers.venv import build_launcher_venv

    try:
        launcher, venv = build_launcher_venv(tmp_path / "host", repo_root)
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"launcher venv unavailable: {exc}")

    project = tmp_path / "project"
    project.mkdir()
    pin_edge_channel(project)

    env = {
        **os.environ,
        "PATH": f"{_stub_ai_hats_on_path(tmp_path)}{os.pathsep}{os.environ['PATH']}",
        ENV_REPO_URL: str(_divergent_source(tmp_path)),
        ENV_AI_HATS_VENV: str(venv),
    }

    master, slave = pty.openpty()  # the wizard path that runs the update needs a TTY
    try:
        proc = subprocess.run(
            [str(launcher), "self", "init", "-p", "claude"],
            cwd=str(project), env=env, stdin=slave,
            capture_output=True, text=True, timeout=420,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("init hung after swapping its own tree")
    finally:
        os.close(slave)
        os.close(master)

    out = proc.stdout + proc.stderr
    assert _SPLIT_SIGNATURE not in out, f"split module set after the update:\n{out}"
    assert proc.returncode == 0, out
