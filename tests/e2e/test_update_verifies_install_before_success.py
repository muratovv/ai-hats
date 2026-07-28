"""E2E: ``self update`` proves the install works before printing success (HATS-1116).

Value under test: an install that *lands* is not an install that *works*. The
updater used to check only uv's exit code and then print ``✓ ai-hats updated``,
so a uv exit 0 that produced an unusable tree sent the user onward with a green
line and a traceback two steps later (HATS-1115).

Retargeted from ``self init`` to ``self update`` (HATS-1215): init no longer
installs anything, so the verify it used to run now lives only on the update
path (``cli/maintenance.py:_run_post_install_verify``).

**The guarantee is fatal (HATS-1239).** Init's verify was fatal (``SystemExit(1)``);
update's post-install verify is now also fatal (``SystemExit(1)``) so an install that
lands but produces an unusable tree exits non-zero, surfacing the failure red.
This test asserts that update exits non-zero, names the breakage, and never prints
success.

Setup (real launcher + real uv install, per ``dev_rule_e2e_gate`` — no stubs):

  - Build an own function-scoped launcher venv via
    :func:`tests.e2e._helpers.venv.build_launcher_venv`. NOT the session-shared
    venv: this test deliberately installs a broken ai-hats into it.
  - Build a broken install source: a ``git clone --shared`` of the repo with the
    working tree's ``src/`` overlaid (so the test reflects uncommitted work too),
    then drop ``PROVIDER_CLAUDE`` from ``constants.py`` while ``assembler.py``
    still imports it — the exact shape of the incident.

Assertion: update exits non-zero, never prints the success line, and names the
failure.

Fail-under-revert: drop the ``_run_post_install_verify`` call in
``cli/maintenance.py`` and update prints ``✓ ai-hats updated`` → the
absence-assertion fails.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from ai_hats.constants import ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV

pytestmark = pytest.mark.install_heavy  # own launcher venv + real uv install

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MISSING_SYMBOL = 'PROVIDER_CLAUDE = "claude"'


def _broken_install_source(dst: Path) -> Path:
    """Clone the repo, overlay the live ``src/``, then remove a symbol siblings import."""
    src = dst / "broken-src"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(REPO_ROOT), str(src)],
        check=True,
        capture_output=True,
        text=True,
    )
    # Overlay the working tree so a dirty checkout is tested, not the last commit.
    shutil.copytree(REPO_ROOT / "src", src / "src", dirs_exist_ok=True)

    constants = src / "src" / "ai_hats" / "constants.py"
    text = constants.read_text()
    if _MISSING_SYMBOL not in text:
        pytest.skip(f"{_MISSING_SYMBOL!r} no longer in constants.py — pick another symbol")
    constants.write_text(text.replace(_MISSING_SYMBOL, "", 1))
    return src


def test_update_does_not_report_success_for_a_broken_install(tmp_path: Path, repo_root: Path):
    """uv exit 0 + unusable tree → red diagnosis and a non-zero exit, never ``✓``."""
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
        "AI_HATS_ALLOW_SELF_UPDATE_IN_TEST": "1",
        ENV_REPO_URL: str(_broken_install_source(tmp_path)),
        ENV_AI_HATS_VENV: str(venv),
    }

    try:
        proc = subprocess.run(
            [str(launcher), "self", "update"],
            cwd=str(project),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=240,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("update hung instead of failing the verify")

    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"update exited 0 for a broken install:\n{out}"
    assert "ai-hats updated" not in out, f"success line printed for a broken install:\n{out}"
    assert "Post-install verify failed" in out, (
        f"broken install failure message was not surfaced:\n{out}"
    )
    assert _MISSING_SYMBOL.split(" =")[0] in out, f"verify did not name the breakage:\n{out}"
