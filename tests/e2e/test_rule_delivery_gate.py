"""HATS-700 — end-to-end behaviour of the rule-delivery pre-commit hook.

``pre-commit-rule-delivery.sh`` is a pure-bash surface the unit suite cannot
exercise. This file drives the script against a real ephemeral git repo. The
wiring scenarios stub the checker through ``AI_HATS_RULE_DELIVERY_CMD`` (offline,
deterministic — they verify changed-files scope, fail-open, override, and
block-on-nonzero, not the checker itself, which the G2 unit test covers). One
final scenario runs the REAL checker (``python -m ai_hats.rule_delivery``) to
prove the module integrates with the hook end to end.

Slow only because of git init + subprocess spin-up.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = (
    REPO_ROOT
    / "library/usage/skills/rule-delivery-gate/git_hooks/pre-commit-rule-delivery.sh"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _make_stub(path: Path, rc: int, message: str = "") -> Path:
    """A fake checker: prints `message` then exits with `rc`."""
    path.write_text(f"#!/usr/bin/env bash\necho {message!r}\nexit {rc}\n")
    path.chmod(0o755)
    return path


def _run_hook(cwd: Path, env: dict | None = None, timeout: int = 20):
    base_env = os.environ.copy()
    for key in (
        "AI_HATS_RULE_DELIVERY_ACK",
        "AI_HATS_RULE_DELIVERY_CMD",
        "PYTHONPATH",
    ):
        base_env.pop(key, None)
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=base_env,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An initialised git repo with a library/ tree and one committed trait."""
    subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
    _git(tmp_path, "config", "user.email", "t@e.x")
    _git(tmp_path, "config", "user.name", "t")
    cfg = tmp_path / "library/core/traits/trait-existing/config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("name: trait-existing\ninjection: |\n  Hello.\n")
    _git(tmp_path, "add", "library/")
    _git(tmp_path, "commit", "-m", "init", "--quiet")
    return tmp_path


def _stage_cfg(repo: Path, relpath: str, body: str) -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    _git(repo, "add", relpath)


_NEW_CFG = "name: trait-new\ninjection: |\n  Body.\n"


# --- stubbed wiring --------------------------------------------------------


@pytest.mark.integration
def test_blocks_when_checker_fails(repo: Path, tmp_path: Path):
    stub = _make_stub(tmp_path / "fail.sh", rc=1, message="see rule `rule_nope`")
    _stage_cfg(repo, "library/core/traits/trait-new/config.yaml", _NEW_CFG)
    res = _run_hook(repo, env={"AI_HATS_RULE_DELIVERY_CMD": f"bash {stub}"})
    assert res.returncode == 1, res.stderr
    assert "[rule-delivery] BLOCKED" in res.stderr
    assert "rule_nope" in res.stderr


@pytest.mark.integration
def test_allows_when_checker_passes(repo: Path, tmp_path: Path):
    stub = _make_stub(tmp_path / "pass.sh", rc=0)
    _stage_cfg(repo, "library/core/traits/trait-new/config.yaml", _NEW_CFG)
    res = _run_hook(repo, env={"AI_HATS_RULE_DELIVERY_CMD": f"bash {stub}"})
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_non_config_change_is_noop(repo: Path, tmp_path: Path):
    """Staging a file that is not a library config.yaml does not trigger the gate."""
    stub = _make_stub(tmp_path / "fail.sh", rc=1, message="would-fail")
    (repo / "README.md").write_text("# Repo\n")
    _git(repo, "add", "README.md")
    res = _run_hook(repo, env={"AI_HATS_RULE_DELIVERY_CMD": f"bash {stub}"})
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_fail_open_when_runner_absent(repo: Path):
    """Missing python/checker → loud no-op (never wedge a commit)."""
    _stage_cfg(repo, "library/core/traits/trait-new/config.yaml", _NEW_CFG)
    res = _run_hook(repo, env={"AI_HATS_RULE_DELIVERY_CMD": "/nonexistent/python-xyz"})
    assert res.returncode == 0, res.stderr
    assert "SKIPPED" in res.stderr


@pytest.mark.integration
def test_ack_override_allows_block(repo: Path, tmp_path: Path):
    stub = _make_stub(tmp_path / "fail.sh", rc=1, message="would-fail")
    _stage_cfg(repo, "library/core/traits/trait-new/config.yaml", _NEW_CFG)
    res = _run_hook(
        repo,
        env={
            "AI_HATS_RULE_DELIVERY_CMD": f"bash {stub}",
            "AI_HATS_RULE_DELIVERY_ACK": "1",
        },
    )
    assert res.returncode == 0, res.stderr
    assert "AI_HATS_RULE_DELIVERY_ACK=1" in res.stderr


# --- real checker end to end ----------------------------------------------


@pytest.mark.integration
def test_real_checker_blocks_dangling_pointer(repo: Path):
    """The actual ai_hats.rule_delivery module, wired through the hook, blocks a
    staged injection that points `see rule X` at an undelivered rule."""
    _stage_cfg(
        repo,
        "library/core/traits/trait-bad/config.yaml",
        "name: trait-bad\ninjection: |\n  Do it — see rule `rule_totally_undelivered`.\n",
    )
    res = _run_hook(
        repo,
        env={
            "AI_HATS_RULE_DELIVERY_CMD": "python3 -m ai_hats.rule_delivery",
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
    )
    assert res.returncode == 1, res.stderr
    assert "rule_totally_undelivered" in res.stderr
