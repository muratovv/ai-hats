"""E2E drift-net: HATS-905 unclaimed-marker sweep on a real ``self bump``.

Synthetic dead-mechanism fixture (HATS-912): a marker with an unregistered
``owner_key`` seeded at a known location (the location outlives its owner),
then a REAL installed-binary bump. Covers the chain the unit suite only
reaches through the ``_sweep_unclaimed_markers`` seam: ``_refresh`` 1b
wiring → gates → adoption → content-proof → trash → console report.
Real binary per ``dev_rule_e2e_gate``; shared session venv (HATS-582).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG
from ai_hats.constants import HOOK_PRE_TOOL_USE


def _run(cmd, *, cwd, env, timeout=60, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _git(project_dir: Path, *args: str, env: dict[str, str]) -> None:
    subprocess.run(
        ["git", *args], cwd=str(project_dir), env=env,
        check=True, capture_output=True, text=True,
    )


def _git_log_count(project_dir: Path, env: dict[str, str]) -> int:
    out = subprocess.run(
        ["git", "log", "--oneline"], cwd=str(project_dir), env=env,
        capture_output=True, text=True, check=True,
    )
    return len([line for line in out.stdout.splitlines() if line.strip()])


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _seed_project(project: Path, env: dict[str, str]) -> dict[str, Path]:
    """Current-shape (v0.7) project with a dead-owner marker + a live surface.

    ``provider: agy`` (no runtime-hook channel) and no active role
    (bare-bump, ``result=None``) on purpose: neither the living git-hooks
    mechanism (``install_git_hooks`` → cleanup) nor ``ensure_runtime_hooks``
    touches the seeded surfaces — only the generic sweeper acts on them.
    """
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\n"
        "provider: agy\n"
        "ai_hats_dir: .agent/ai-hats\n"
    )
    (project / ".agent" / "ai-hats").mkdir(parents=True)

    # Dead-owner marker: hashed owner_key convention (HATS-911).
    event_d = project / ".githooks" / "pre-commit.d"
    event_d.mkdir(parents=True)
    retired = event_d / "retired.sh"
    retired.write_bytes(b"#!/bin/sh\nexit 0\n")
    edited = event_d / "edited.sh"
    original_edited = b"#!/bin/sh\necho materialized\n"
    edited.write_bytes(b"#!/bin/sh\necho user rewrote me\n")  # hash mismatch
    marker = project / ".githooks" / ".ai-hats-manifest"
    marker.write_text(
        "# ai-hats-owner: retired-mech\n"
        f"{_digest(retired.read_bytes())}  pre-commit.d/retired.sh\n"
        f"{_digest(original_edited)}  pre-commit.d/edited.sh\n"
    )

    # Live surface: runtime-hooks is registered in the running binary, so
    # the sweeper must skip this file entirely (byte-identical after bump).
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({
        "hooks": {
            HOOK_PRE_TOOL_USE: [
                {
                    "matcher": "Bash",
                    "_ai_hats_managed": "ai-hats:hats-437",
                    "hooks": [{"type": "command", "command": "guard.sh"}],
                },
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": "user-own.sh"}],
                },
            ]
        }
    }, indent=2) + "\n")

    _git(project, "init", "-q", "-b", "main", env=env)
    _git(project, "config", "user.email", "test@example.com", env=env)
    _git(project, "config", "user.name", "Test", env=env)
    _git(project, "add", "-A", env=env)
    _git(project, "commit", "-q", "-m", "seed", env=env)
    return {
        "marker": marker,
        "retired": retired,
        "edited": edited,
        "settings": settings,
    }


@pytest.fixture
def swept_env(shared_launcher, tmp_path):
    """Copy of the hermetic shared env with a per-test trash dir, so the
    test can assert the swept victim actually landed in trash."""
    _launcher, env, _venv = shared_launcher
    trash = tmp_path / "trash"
    env = dict(env)
    env["AI_HATS_TRASH_DIR"] = str(trash)
    return env, trash


@pytest.mark.integration
def test_e2e_bump_sweeps_dead_owner_marker(swept_env, repo_root, tmp_path):
    env, trash = swept_env
    # ``provider: agy`` is an out-of-tree surface (packages/surfaces/agy), not a
    # builtin — install it into the shared launcher venv so the seed resolves
    # via the entry-point registry (HATS-1093).
    subprocess.run(
        ["uv", "pip", "install", "--python", f"{env[ENV_AI_HATS_VENV]}/bin/python",
         "-e", str(repo_root / "packages" / "surfaces" / "agy")],
        check=True, capture_output=True, text=True,
    )
    project = tmp_path / "proj"
    project.mkdir()
    paths = _seed_project(project, env)
    settings_before = paths["settings"].read_bytes()
    before_commits = _git_log_count(project, env)

    res = _run(
        [f"{env[ENV_AI_HATS_VENV]}/bin/python", "-m", "ai_hats._bump_internal"],
        cwd=project, env=env,
    )

    # Content-proven victim: gone from disk, recoverable from trash.
    assert not paths["retired"].exists()
    assert list(trash.rglob("retired.sh")), "victim not found in trash"
    assert "retired-mech" in res.stdout, res.stdout
    assert "pre-commit.d/retired.sh" in res.stdout, res.stdout
    assert "recoverable" in res.stdout, res.stdout

    # User-edited victim: kept on disk + WARN on stderr.
    assert paths["edited"].read_bytes() == b"#!/bin/sh\necho user rewrote me\n"
    assert "edited.sh" in res.stderr, res.stderr
    assert "left in place" in res.stderr, res.stderr

    # Marker rewritten: header + contested line stay, swept line gone.
    marker_text = paths["marker"].read_text()
    assert marker_text.splitlines()[0] == "# ai-hats-owner: retired-mech"
    assert "pre-commit.d/edited.sh" in marker_text
    assert "pre-commit.d/retired.sh" not in marker_text

    # Live surface untouched — byte-identical.
    assert paths["settings"].read_bytes() == settings_before

    # Bump never commits.
    assert _git_log_count(project, env) == before_commits


@pytest.mark.integration
def test_e2e_second_bump_repeats_warn_for_contested_entry(swept_env, tmp_path):
    """Q6 (HATS-905): an unresolved ownership conflict re-WARNs on every
    bump — deliberately, until the user resolves it. State stays stable."""
    env, _trash = swept_env
    project = tmp_path / "proj"
    project.mkdir()
    paths = _seed_project(project, env)

    _run(
        [f"{env[ENV_AI_HATS_VENV]}/bin/python", "-m", "ai_hats._bump_internal"],
        cwd=project, env=env,
    )
    marker_after_first = paths["marker"].read_bytes()

    res = _run(
        [f"{env[ENV_AI_HATS_VENV]}/bin/python", "-m", "ai_hats._bump_internal"],
        cwd=project, env=env,
    )

    assert "edited.sh" in res.stderr, res.stderr
    assert "left in place" in res.stderr, res.stderr
    assert "recoverable" not in res.stdout, res.stdout  # nothing proven left
    assert paths["marker"].read_bytes() == marker_after_first
    assert paths["edited"].exists()
