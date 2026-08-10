"""e2e (HATS-1473)

flow:   a developer starting a session when stale sibling cache keys exist in cache dir
cmds:   ai-hats execute -r assistant
expect: session initialization sweeps orphan cache keys older than TTL while preserving
        active keys
why:    session startup must garbage-collect stale session cache directories
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from _helpers.hitl import drive_bare_hitl

pytestmark = pytest.mark.integration


def _make_key(cache_home: Path, name: str, *, age_days: float) -> Path:
    key = cache_home / name
    (key / "sessions").mkdir(parents=True, exist_ok=True)
    stamp = time.time() - age_days * 86400
    os.utime(key / "sessions", (stamp, stamp))
    os.utime(key, (stamp, stamp))
    return key


def test_session_start_reaps_stale_sibling_keys(tmp_venv_project, requires_claude_auth) -> None:
    cache_home = Path(tmp_venv_project.env["AI_HATS_CACHE_HOME"])
    cache_home.mkdir(parents=True, exist_ok=True)
    stale = _make_key(cache_home, "gone-deadbeef", age_days=30)
    fresh = _make_key(cache_home, "live-deadbeef", age_days=0)

    tmp_venv_project.run(
        "self", "init", "-r", "assistant", "-p", "claude", "--no-update", timeout=120
    ).expect_ok()
    drive_bare_hitl(tmp_venv_project, role="assistant").expect_no_hang().expect_exit_in({0, 130})

    assert not stale.exists(), "a key untouched past the TTL must be reclaimed"
    assert fresh.exists(), "a recently touched key must survive"
