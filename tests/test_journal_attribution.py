"""Who a guard-journal record names."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PY = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/hooks/bypass_journal.py"
ENV_JOURNAL_DIR = "AI_HATS_JOURNAL_DIR"
ENV_SESSION_IDENTITY = "AI_HATS_SESSION_IDENTITY"
ENV_SESSION_ID = "AI_HATS_SESSION_ID"


def _envelope(session_id: str, **over: object) -> str:
    payload = {
        "v": 1,
        "id": session_id,
        "role": "maintainer",
        "provider": "claude",
        "project_dir": "/tmp/proj",  # noqa: S108
        "session_dir": "/tmp/proj/.agent/ai-hats/sessions/runs/session_x",  # noqa: S108
    }
    payload.update(over)
    return json.dumps(payload)


def _record(journal_dir: Path, **env_extra: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in (ENV_SESSION_IDENTITY, ENV_SESSION_ID)}
    env[ENV_JOURNAL_DIR] = str(journal_dir)
    env.update(env_extra)
    return subprocess.run(  # noqa: S603
        [sys.executable, str(PY), "record", "--kind", "hatch", "--reason", "R", "--hook", "p.py"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _row(journal_dir: Path) -> dict:
    return json.loads((journal_dir / "bypasses.jsonl").read_text().splitlines()[-1])


def test_the_envelope_names_the_session(tmp_path: Path) -> None:
    proc = _record(tmp_path, **{ENV_SESSION_IDENTITY: _envelope("sid-from-envelope")})
    assert proc.returncode == 0, proc.stderr
    assert _row(tmp_path)["session_id"] == "sid-from-envelope"


def test_the_scalar_still_names_it_when_no_envelope_rode_along(tmp_path: Path) -> None:
    proc = _record(tmp_path, **{ENV_SESSION_ID: "sid-from-scalar"})
    assert proc.returncode == 0, proc.stderr
    assert _row(tmp_path)["session_id"] == "sid-from-scalar"


@pytest.mark.parametrize(
    ("raw",),
    [
        ("{nonsense",),
        ("[1, 2]",),
        ('{"v": 2, "id": "x"}',),
    ],
)
def test_an_envelope_it_cannot_trust_is_refused_out_loud(tmp_path: Path, raw: str) -> None:
    proc = _record(tmp_path, **{ENV_SESSION_IDENTITY: raw, ENV_SESSION_ID: "sid-from-scalar"})
    assert proc.returncode == 0, proc.stderr
    assert _row(tmp_path)["session_id"] == "sid-from-scalar"
    assert "identity" in proc.stderr.lower(), proc.stderr
