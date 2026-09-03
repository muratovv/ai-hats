"""e2e (HATS-1606)

flow:   plant a deliberately foreign AI_HATS_DIR + pin pair and read WHICH
        project the launcher says it is foreign TO — that word is the
        launcher's resolved root, printed by its own trust procedure.
cmds:
    AI_HATS_DIR=<foreign> AI_HATS_PROJECT_DIR=<foreign> bash scripts/ai-hats-launcher --version
expect: the printed root equals `resolve_root`'s answer for the same cwd —
        yaml-only markers and the conditional worktree hop included.
why:    the launcher is a sanctioned bash mirror; parity is held by this
        conformance test, not shared code (ADR-0014 / ADR-0025 D3).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from _helpers.git import git

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "scripts" / "ai-hats-launcher"

_FOREIGN_RE = re.compile(r"foreign to (?P<root>\S+); dropping it", re.M)


def _launcher_resolved_root(cwd: Path) -> str:
    """The root the launcher's own trust procedure names for this cwd."""
    foreign = cwd / "definitely-foreign"
    foreign.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.pop("AI_HATS_SESSION_IDENTITY", None)
    env.pop("AI_HATS_SESSION_ID", None)
    env["AI_HATS_DIR"] = str(foreign / "base")
    env["AI_HATS_PROJECT_DIR"] = str(foreign)
    # The drop message prints before any venv work; the run itself may then
    # fail or hang on provisioning — bound it and read stderr only.
    res = subprocess.run(  # noqa: S603 — fixed argv, launcher under test
        ["bash", str(LAUNCHER), "--version"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    m = _FOREIGN_RE.search(res.stderr)
    assert m, f"launcher printed no foreign-pin drop line:\n{res.stderr}"
    return m.group("root")


def _python_resolved_root(cwd: Path) -> str:
    from ai_hats_core.layout import ForeignPinPolicy, resolve_root

    return str(resolve_root(cwd, {}, on_foreign_pin=ForeignPinPolicy.WARN_AND_IGNORE))


def test_yaml_only_project_agrees(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "ai-hats.yaml").write_text("schema_version: 4\nai_hats_dir: .agent/ai-hats\n")
    assert _launcher_resolved_root(proj) == _python_resolved_root(proj)


def test_worktree_hop_agrees(tmp_path: Path) -> None:
    """1a: onboarded main → both hop; the case no prior fixture reached."""
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-b", "master")
    git(main, "config", "user.email", "t@t")
    git(main, "config", "user.name", "t")
    (main / "README").write_text("x")
    git(main, "add", ".")
    git(main, "commit", "-m", "seed")
    (main / ".agent").mkdir()
    wt = tmp_path / "wt"
    git(main, "worktree", "add", str(wt))
    (wt / ".agent").mkdir()  # the stray copy a task branch would carry

    assert _launcher_resolved_root(wt) == _python_resolved_root(wt) == str(main.resolve())
