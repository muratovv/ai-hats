"""Tests for relay Makefile targets."""

from __future__ import annotations

import subprocess
from pathlib import Path

RELAY_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = RELAY_DIR.parent


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
