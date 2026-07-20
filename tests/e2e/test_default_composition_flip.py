"""e2e: the default backlog-manager flip + three-backlog bash resolution (HATS-1054).

Two real-binary checks against a session-shared launcher venv (built from THIS
worktree source, so it carries the trait-agent flip under test):

* **Composition flip (R1/R11).** A fresh ``self init -r assistant`` project's
  composed component list (``ai-hats list tokens``) carries the ``hatrack`` skill
  and NO LONGER carries the classic ``backlog-manager``.

* **Three-backlog resolution (R12).** On a sandbox with a task + a migrated HYP
  catalog + a migrated PROP catalog, the real ``rack`` binary resolves each prefix
  to its card, lists the ``hyp`` / ``proposal`` groups in ``--help``, runs each
  group's ``ls``, and fails an unknown prefix with a typed error naming the
  configured prefixes.

Fail-under-revert (per ``dev_rule_e2e_gate`` §4): reverting the
``trait-agent`` skill swap (``hatrack`` → ``backlog-manager``) makes the flip
assertions red — ``list tokens`` would drop ``hatrack`` and re-add
``backlog-manager``. Proven by reverting the swap and re-composing (recorded on
the task card); the same composition feeds this test's ``list tokens`` output.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
AI_HATS_SUB = Path(".agent") / "ai-hats"


def _run(cmd, *, cwd, env, timeout, expect_exit=None, check=False):
    result = subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"{cmd} exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_git_project(root: Path) -> None:
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "ai-hats.yaml").write_text("task_prefix: SBX\n")
    (root / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    (root / TASKS_SUB).mkdir(parents=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init", "--allow-empty")


def _seed_flat_catalogs(ai_hats_dir: Path) -> None:
    """Write one flat HYP (old flat dir) + one flat PROP so the migrator has input."""
    hyp = ai_hats_dir / "tracker" / "hypotheses"
    prop = ai_hats_dir / "tracker" / "backlog" / "proposals"
    hyp.mkdir(parents=True, exist_ok=True)
    prop.mkdir(parents=True, exist_ok=True)
    (hyp / "HYP-001.yaml").write_text(yaml.safe_dump({
        "id": "HYP-001", "title": "sandbox hyp", "status": "active",
        "created": "2026-01-01", "source_task": "SBX-001", "hypothesis": "h",
        "validation_log": [],
    }))
    (prop / "PROP-001.yaml").write_text(yaml.safe_dump({
        "id": "PROP-001", "created": "2026-01-01T00:00:00Z", "title": "sandbox prop",
        "category": "rule", "target": "x", "description": "d", "rationale": "r",
        "related_hypotheses": [], "votes": [], "status": "open",
    }))


def test_default_composition_flip(shared_launcher, tmp_path: Path):
    launcher, base_env, venv = shared_launcher
    rack = venv / "bin" / "rack"
    py = venv / "bin" / "python"
    assert rack.is_file(), "ai-hats-rack must install the `rack` console script"

    env = dict(base_env)
    # The runner's PYTHONPATH would shadow the venv install; drop it (HATS-685).
    env.pop("PYTHONPATH", None)
    env_wide = {**env, "COLUMNS": "220"}  # keep the token table from truncating names

    # ============ Part 1: the composition flip (R1/R11) ============
    proj = tmp_path / "compose"
    proj.mkdir()
    _run([launcher, "self", "init", "-p", "claude", "-r", "assistant"],
         cwd=proj, env=env, timeout=120, check=True)

    tokens = _run([launcher, "list", "tokens", "assistant", "--approx"],
                  cwd=proj, env=env_wide, timeout=60, expect_exit=0)
    listed = tokens.stdout + tokens.stderr
    # Fail-under-revert: with the trait-agent swap reverted, `hatrack` drops out
    # and `backlog-manager` comes back — both assertions flip to red.
    assert "hatrack" in listed, (
        f"composed agent role must carry the `hatrack` skill after the flip:\n{listed}"
    )
    assert "backlog-manager" not in listed, (
        f"composed agent role must NOT carry `backlog-manager` after the flip:\n{listed}"
    )

    # ============ Part 2: three-backlog bash resolution (R12) ============
    sbx = tmp_path / "sbx"
    sbx.mkdir()
    _init_git_project(sbx)
    ai_hats_dir = sbx / AI_HATS_SUB

    created = _run([rack, "create", "sandbox task", "--role", "assistant"],
                   cwd=sbx, env=env, timeout=90, expect_exit=0)
    assert "SBX-001" in created.stdout, created.stdout

    # Seed flat HYP/PROP, then migrate them into the normalized catalogs via the
    # real migrator (dogfood): HYP → tracker/backlog/hypotheses, PROP in-place.
    _seed_flat_catalogs(ai_hats_dir)
    _run([py, "-m", "ai_hats_rack.migrate", str(ai_hats_dir)],
         cwd=sbx, env=env, timeout=60, expect_exit=0)
    assert (ai_hats_dir / "tracker" / "backlog" / "hypotheses" / "HYP-001" / "task.yaml").is_file()
    assert (ai_hats_dir / "tracker" / "backlog" / "proposals" / "PROP-001" / "task.yaml").is_file()

    # `rack context <id>` resolves each of the three prefixes to its own card.
    for cid in ("SBX-001", "HYP-001", "PROP-001"):
        ctx = _run([rack, "context", cid], cwd=sbx, env=env, timeout=60, expect_exit=0)
        assert cid in ctx.stdout, f"`rack context {cid}` did not resolve its card:\n{ctx.stdout}"

    # `rack --help` lists the per-backlog groups once the siblings are mounted.
    help_out = _run([rack, "--help"], cwd=sbx, env=env, timeout=60, expect_exit=0)
    assert "hyp" in help_out.stdout, f"`rack --help` must list the hyp group:\n{help_out.stdout}"
    assert "proposal" in help_out.stdout, f"`rack --help` must list the proposal group:\n{help_out.stdout}"

    # Each group resolves and exposes its verbs on the migrated data (the per-backlog
    # groups carry create/update + extension verbs — HATS-1036 — not a base `ls`).
    _run([rack, "hyp", "--help"], cwd=sbx, env=env, timeout=60, expect_exit=0)
    _run([rack, "proposal", "--help"], cwd=sbx, env=env, timeout=60, expect_exit=0)
    # The base `ls` lists/walks each migrated catalog by prefix routing.
    hyp_ls = _run([rack, "ls", "HYP-001"], cwd=sbx, env=env, timeout=60, expect_exit=0)
    assert "HYP-001" in hyp_ls.stdout, hyp_ls.stdout
    prop_ls = _run([rack, "ls", "PROP-001"], cwd=sbx, env=env, timeout=60, expect_exit=0)
    assert "PROP-001" in prop_ls.stdout, prop_ls.stdout

    # An unknown prefix is a typed refusal that names the configured prefixes.
    unknown = _run([rack, "context", "ZZZ-1"], cwd=sbx, env=env, timeout=60)
    assert unknown.returncode != 0, "unknown prefix must fail"
    combined = unknown.stdout + unknown.stderr
    assert "Traceback" not in unknown.stderr, f"unknown-prefix refusal must be typed:\n{combined}"
    assert "ZZZ" in combined, f"error must name the unknown prefix:\n{combined}"
    assert "HYP" in combined and "PROP" in combined, (
        f"error must list the configured prefixes (all three backlogs mounted):\n{combined}"
    )
