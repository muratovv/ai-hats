"""e2e (HATS-1606)

flow:   one cd, two stacks: in five layouts a REAL `python -c` subprocess
        asks the integrator's entry (`resolve_project`) and rack's
        `find_project_root` for the project root from the same cwd.
cmds:
    python -c "from ai_hats.cli._entry import resolve_project; ..."
    python -c "from ai_hats_rack.resolver import find_project_root; ..."
expect: byte-identical roots — the divergences of counter 1 (yaml-only
        project, stray ancestor) and R8 (linked worktree whose main
        checkout is NOT onboarded) are closed.
why:    the historical resolvers disagreed on the marker table and the hop;
        `ai-hats wait` answered differently from its neighbours in one cd.
        The probes import each stack's public resolver inside a real
        subprocess rather than scraping a human-formatted CLI table: no
        stock command prints the root, and the subject is the semantics.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.git import git

pytestmark = [pytest.mark.integration, pytest.mark.rack]

REPO_ROOT = Path(__file__).resolve().parents[2]

_INTEGRATOR_PROBE = (
    "from ai_hats.cli._entry import resolve_project;print(resolve_project().layout.root)"
)
_RACK_PROBE = (
    "import pathlib;"
    "from ai_hats_rack.resolver import find_project_root;"
    "print(find_project_root(pathlib.Path.cwd()))"
)


def _probe(code: str, cwd: Path) -> str:
    from _helpers.env import checkout_pythonpath

    env = dict(os.environ)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    for leak in (
        "AI_HATS_PROJECT_DIR",
        "AI_HATS_DIR",
        "AI_HATS_SESSION_IDENTITY",
        "AI_HATS_SESSION_ID",
    ):
        env.pop(leak, None)
    res = subprocess.run(  # noqa: S603 — fixed argv, our own interpreter
        [sys.executable, "-c", code],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, f"probe failed in {cwd}:\n{res.stderr}"
    return res.stdout.strip()


def _parity(cwd: Path) -> tuple[str, str]:
    return _probe(_INTEGRATOR_PROBE, cwd), _probe(_RACK_PROBE, cwd)


def test_yaml_only_project_resolves_identically(tmp_path: Path) -> None:
    """counter 1: the integrator's old walk-up never accepted ai-hats.yaml."""
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "ai-hats.yaml").write_text("schema_version: 4\nai_hats_dir: .agent/ai-hats\n")
    ours, rack = _parity(proj / "sub")
    assert ours == rack == str(proj.resolve())


def test_agent_only_project_resolves_identically(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / ".agent").mkdir()
    ours, rack = _parity(proj / "sub")
    assert ours == rack == str(proj.resolve())


def test_stray_ancestor_does_not_capture(tmp_path: Path) -> None:
    """A forgotten .agent above the project must not swallow it (counter 1)."""
    (tmp_path / ".agent").mkdir()  # the stray
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "ai-hats.yaml").write_text("schema_version: 4\nai_hats_dir: .agent/ai-hats\n")
    ours, rack = _parity(proj / "sub")
    assert ours == rack == str(proj.resolve())


def test_linked_worktree_with_unonboarded_main(tmp_path: Path) -> None:
    """R8/1a: the hop is CONDITIONAL — a main checkout with no markers means
    the worktree's own markers answer, identically on both stacks. This exact
    case was unreachable in every prior fixture (ADR-0025 D2)."""
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-b", "master")
    git(main, "config", "user.email", "t@t")
    git(main, "config", "user.name", "t")
    (main / "ai-hats.yaml").write_text("schema_version: 4\nai_hats_dir: .agent/ai-hats\n")
    git(main, "add", ".")
    git(main, "commit", "-m", "seed")
    wt = tmp_path / "wt"
    git(main, "worktree", "add", str(wt))
    # the main checkout is NOT onboarded beyond the tracked yaml… strip it:
    (main / "ai-hats.yaml").unlink()  # markers now live ONLY in the worktree

    ours, rack = _parity(wt)
    assert ours == rack == str(wt.resolve())


def test_linked_worktree_with_onboarded_main_hops(tmp_path: Path) -> None:
    """The complement: an onboarded main checkout wins over the worktree's
    tracked marker copy — on both stacks."""
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-b", "master")
    git(main, "config", "user.email", "t@t")
    git(main, "config", "user.name", "t")
    (main / "ai-hats.yaml").write_text("schema_version: 4\nai_hats_dir: .agent/ai-hats\n")
    git(main, "add", ".")
    git(main, "commit", "-m", "seed")
    (main / ".agent").mkdir()
    wt = tmp_path / "wt"
    git(main, "worktree", "add", str(wt))

    ours, rack = _parity(wt)
    assert ours == rack == str(main.resolve())
