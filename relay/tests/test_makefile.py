"""Tests for relay Makefile targets."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

RELAY_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = RELAY_DIR.parent


def _run(target: str, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["make", "-C", str(RELAY_DIR), target, *extra],
        capture_output=True,
        text=True,
        env=env,
    )


def test_a_tokenless_run_is_refused_with_the_way_out():
    """The targets have to stay in step with the relay: since HATS-1194 the broker does
    not start unauthenticated, so a target that shells out without a token would fail
    with an argparse dump the moment it is used. Help text and dry-run assertions cannot
    see that — only actually invoking it can."""
    env = {k: v for k, v in os.environ.items() if k != "HATS_RELAY_TOKEN"}
    for target in ("run-server", "run-client"):
        res = _run(target, env=env)
        assert res.returncode != 0, f"{target} started without a token"
        assert "HATS_RELAY_TOKEN is not set" in res.stderr, res.stderr
        assert "openssl rand" in res.stderr, f"{target} refuses without saying how to fix it"


def test_the_token_is_not_put_on_the_command_line():
    """An argv token is readable in `ps` by anyone else on the box, so it rides the
    environment the CLI already reads."""
    env = {**os.environ, "HATS_RELAY_TOKEN": "from-the-environment"}
    res = _run("run-server", "-n", env=env)
    assert res.returncode == 0, res.stderr
    assert "--token" not in res.stdout
    assert "from-the-environment" not in res.stdout


def test_relay_makefile_help():
    """make -C relay help displays run-server and run-client targets."""
    res = subprocess.run(
        ["make", "-C", str(RELAY_DIR), "help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "run-server" in res.stdout
    assert "run-client" in res.stdout


def test_root_makefile_relay_help():
    """make help in repo root displays relay-server and relay-client targets."""
    res = subprocess.run(
        ["make", "-C", str(ROOT_DIR), "help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "relay-server" in res.stdout
    assert "relay-client" in res.stdout


def test_check_venv_failure():
    """check-venv fails with informative error when VENV is invalid/missing."""
    res = subprocess.run(
        ["make", "-C", str(RELAY_DIR), "check-venv", "VENV=/nonexistent/venv"],
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert "relay/.venv missing or incomplete" in res.stderr
    assert "cd relay && python3 -m venv .venv" in res.stderr


def test_dry_run_relay_targets():
    """Root Makefile relay-server and relay-client delegate with ARGS to relay/Makefile."""
    res_server = subprocess.run(
        ["make", "-C", str(ROOT_DIR), "relay-server", "ARGS=--verbose", "-n"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "run-server" in res_server.stdout
    assert "--verbose" in res_server.stdout

    res_client = subprocess.run(
        ["make", "-C", str(ROOT_DIR), "relay-client", "ARGS=--role reviewer", "-n"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "run-client" in res_client.stdout
    assert "--role reviewer" in res_client.stdout
