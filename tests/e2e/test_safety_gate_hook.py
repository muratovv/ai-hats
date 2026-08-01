"""HATS-1372 — the safety gate actually denies.

This is a `deny`-class PreToolUse hook, the highest-blast-radius kind, and until
now only wiring tests existed: they proved the hook was materialized into the
settings, never that it refuses anything. A gate with no test that fires is a
gate nobody has checked.

Per `dev_rule_e2e_gate` each case spawns the hook as a real subprocess and
speaks the PreToolUse JSON protocol over stdin.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard/hooks/safety_gate.py"
)


def _decide(command: str, *, env_extra: dict[str, str] | None = None) -> dict:
    """Run the hook on a Bash payload; return its decision ({} when it allows)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")}
    env.update(env_extra or {})
    res = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert res.returncode == 0, res.stderr
    if not res.stdout.strip():
        return {}
    return json.loads(res.stdout)["hookSpecificOutput"]


def _denied(command: str, **kw) -> str:
    out = _decide(command, **kw)
    assert out.get("permissionDecision") == "deny", f"{command!r} was ALLOWED: {out}"
    return out["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf /*",
        "rm -rf ~",
        "sudo rm -rf /",
        "ls && rm -rf /",
    ],
)
def test_catastrophic_paths_are_denied(command):
    assert "filesystem root" in _denied(command)


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf app.db",
        "rm data/users.sqlite3",
        "rm -f dump.sql",
        "rm terraform.tfstate",
        "rm -rf volumes/",
        "rm .env",
        # HATS-1430: recorded experiment runs are gitignored and irreproducible,
        # so the repo cannot restore them — the thing the list is a proxy for.
        "rm -rf experiments/hatrack-hardening/control/runs",
    ],
)
def test_protected_data_is_denied(command):
    assert "protected data" in _denied(command)


def test_in_place_sed_is_denied():
    assert "in place" in _denied("sed -i 's/a/b/' file.py")


def test_destructive_sql_through_a_client_is_denied():
    assert "destructive SQL" in _denied('psql -c "DROP TABLE users"')


def test_a_filesystem_formatter_is_denied():
    assert "formats a filesystem" in _denied("mkfs.ext4 /dev/sda1")


def test_dd_writing_to_a_device_is_denied():
    assert "destroys a disk" in _denied("dd if=/dev/zero of=/dev/sda")


def test_granting_yolo_inline_is_denied():
    assert "cannot be granted inline" in _denied("AI_HATS_YOLO=1 rm -rf app.db")


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "rm -rf /tmp/scratch",
        "rm build/artifact.txt",
        "sed 's/a/b/' file.py",
        "echo 'drop table users'",
    ],
)
def test_benign_commands_are_allowed(command):
    """A gate that denies everything is as useless as one that denies nothing."""
    assert _decide(command) == {}


def test_the_ack_opens_protected_data_but_never_the_root():
    ack = {"AI_HATS_DESTRUCTIVE_ACK": "1"}

    assert _decide("rm -rf app.db", env_extra=ack) == {}
    assert "No consent flag overrides this" in _denied("rm -rf /", env_extra=ack)


def test_the_yolo_switch_disables_the_gate():
    """Documented kill switch — pinned so it cannot be removed silently."""
    assert _decide("rm -rf /", env_extra={"AI_HATS_YOLO": "1"}) == {}
