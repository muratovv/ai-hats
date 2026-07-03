"""HATS-492 — ``ai-hats task create/update --description-file F`` reads F
verbatim, sidestepping the shell command-substitution / heredoc hazards that
silently truncate ``-d "$(cat <<EOF …)"``.

Pattern (subprocess + ``python -m ai_hats`` with an explicit ``PYTHONPATH=src``)
mirrors ``tests/e2e/test_task_transition_branch_exists.py`` — checkout-independent,
runs the worktree's own ``ai_hats``, no installed ``ai-hats`` binary required.

dev_rule_e2e_gate (HATS-492 touches ``src/ai_hats/cli/task.py``): this is the
gated test. Fail-under-revert: drop the ``--description-file`` option from
``task_create`` → ``task create --description-file`` exits 2 ("No such option")
and the round-trip assertion fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC = REPO_ROOT / "src"

# Markdown body that shell parsing would mangle: backticks, ``$(...)``, an
# unbalanced paren, a nested ``` fence, and a bare ``EOF`` terminator line.
GNARLY = (
    "## Repro\n"
    "```python\n"
    "x = `backtick` + $(whoami)\n"
    "y = (unbalanced\n"
    "```\n"
    "EOF\n"
    "field: value\n"
)


def _run_hats(
    project_dir: Path, *args: str, timeout: float = 30.0
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m ai_hats <args>`` against the current checkout's src."""
    env = os.environ.copy()
    from _helpers.env import checkout_pythonpath

    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, existing)
    return subprocess.run(
        [sys.executable, "-m", "ai_hats", *args],
        cwd=str(project_dir),
        capture_output=True, text=True, env=env, timeout=timeout,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Minimal ai-hats project (no git needed for create/show)."""
    proj = tmp_path / "project"
    proj.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(proj / "ai-hats.yaml")
    Assembler(proj).init()
    return proj


def test_create_description_file_roundtrips_verbatim(project: Path) -> None:
    """`task create --description-file F` → `task show` returns F's body intact."""
    desc = project / "desc.md"
    desc.write_text(GNARLY)

    r = _run_hats(
        project, "task", "create", "Gnarly",
        "--id", "HATS-EDF-1", "--description-file", str(desc),
    )
    assert r.returncode == 0, f"create failed: {r.stdout}\n{r.stderr}"

    shown = _run_hats(project, "task", "show", "HATS-EDF-1")
    assert shown.returncode == 0, shown.stderr
    # Every distinctive line survived — nothing truncated at a shell boundary.
    for token in (
        "x = `backtick` + $(whoami)",
        "y = (unbalanced",
        "EOF",
        "field: value",
    ):
        assert token in shown.stdout, f"missing {token!r} in:\n{shown.stdout}"


def test_create_description_file_conflicts_with_d(project: Path) -> None:
    """`-d` + `--description-file` together → friendly UsageError (exit 2)."""
    desc = project / "desc.md"
    desc.write_text("from file")

    r = _run_hats(
        project, "task", "create", "Clash", "--id", "HATS-EDF-2",
        "-d", "inline", "--description-file", str(desc),
    )
    assert r.returncode == 2, (
        f"expected UsageError exit 2, got {r.returncode}\n{r.stdout}{r.stderr}"
    )
    assert "mutually exclusive" in (r.stdout + r.stderr).lower()
