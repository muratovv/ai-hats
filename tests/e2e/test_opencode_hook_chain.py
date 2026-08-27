"""e2e (HATS-1858)

flow:   an operator runs an opencode-provider role and expects the composed
        gates to reach tool calls through the plugin ai-hats installs
cmds:
    node <session>/opencode/plugin/ai-hats-hooks.mjs   # via a stand-in host
expect: a `permissionDecision` refusal throws the call out, a nudge reaches the
        console with its author, a vanished manifest refuses while naming the
        hatch, and that hatch lets a human past
why:    the plugin read a verdict off an exit code and understood two shapes,
        one of which no shipped hook emits — seven of eight gates could refuse
        and be waved through — and it returned {} on a missing manifest, so a
        session that lost one ran with every gate off for its whole life
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from _helpers.sessions import stand_in_session
from _helpers.hook_chain import run_opencode_dispatch

SESSION_ID = "sid-opencode-chain"
OFF_LIMITS = "/etc/passwd"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs a JS runtime")


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _emit(**spoken) -> str:
    doc = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **spoken}})
    return f"cat >/dev/null\nprintf '%s' '{doc}'\n"


@pytest.fixture
def opencode_chain(tmp_path: Path) -> SimpleNamespace:
    """A pinned opencode session: an audit that nudges, then a guard that
    refuses one path — the chain a single-hook test cannot see."""
    from ai_hats.surfaces.opencode.runtime_hooks import PLUGIN_ASSET, plugin_source

    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    mirror = cache / "opencode-xdg" / "opencode" / "skills"
    ledger = tmp_path / "ledger.txt"

    audit = _script(
        mirror / "audit.sh",
        f"#!/bin/sh\necho audit >> '{ledger}'\n" + _emit(additionalContext="prefer Read"),
    )
    guard = _script(
        mirror / "guard.sh",
        f"#!/bin/sh\n"
        f"echo guard >> '{ledger}'\n"
        f'if grep -q "{OFF_LIMITS}" 2>/dev/null; then\n'
        f"  printf '%s' '"
        + json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"{OFF_LIMITS} is off limits",
                }
            }
        )
        + "'\nfi\n",
    )

    plugin = cache / "opencode" / "plugin" / PLUGIN_ASSET
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(plugin_source(), encoding="utf-8")

    manifest = cache / "opencode" / "hooks.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "session": {"id": SESSION_ID, "ai_hats_dir": str(project / ".agent" / "ai-hats")},
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Read", "command": str(audit), "tag": "ai-hats:audit"},
                        {"matcher": "Read", "command": str(guard), "tag": "ai-hats:guard"},
                    ]
                },
                "permissions": [],
            }
        )
    )
    env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="opencode")
    env |= {
        "AI_HATS_SESSION_CACHE_DIR": str(cache),
        "AI_HATS_PROJECT_DIR": str(project),
        "AI_HATS_PYTHON": sys.executable,
    }
    return SimpleNamespace(project=project, env=env, ledger=ledger, manifest=manifest)


def _fired(chain: SimpleNamespace) -> list[str]:
    return chain.ledger.read_text().split() if chain.ledger.is_file() else []


def test_a_refusal_in_the_dialect_every_shipped_hook_speaks_stops_the_call(
    opencode_chain,
) -> None:
    """`permissionDecision` appeared nowhere in the plugin, so this exact reply
    — the one all eight shipped gates emit — was read as consent."""
    said = run_opencode_dispatch(
        opencode_chain.project,
        opencode_chain.env,
        tool="read",
        args={"filePath": OFF_LIMITS, "file_path": OFF_LIMITS},
    )

    assert said["registered"], "the plugin bound no tool hooks at all"
    assert said["threw"], f"the call was waved through: {said}"
    assert OFF_LIMITS in said["message"]
    assert _fired(opencode_chain) == ["audit", "guard"], (
        f"the composed chain did not run in order; fired: {_fired(opencode_chain)}"
    )


def test_a_nudge_reaches_the_console_with_its_author(opencode_chain) -> None:
    """`additionalContext` appeared nowhere either, so advice was discarded."""
    said = run_opencode_dispatch(
        opencode_chain.project,
        opencode_chain.env,
        tool="read",
        args={"filePath": "/tmp/ok.txt", "file_path": "/tmp/ok.txt"},
    )

    assert not said["threw"], said
    assert "prefer Read" in said["console"], said["console"]
    assert "ai-hats:audit" in said["console"]


def test_a_vanished_manifest_refuses_instead_of_disabling_every_gate(
    opencode_chain,
) -> None:
    """The plugin returned {} here, so a session that lost its manifest before
    the plugin loaded ran with every gate off for its whole life."""
    opencode_chain.manifest.unlink()

    said = run_opencode_dispatch(
        opencode_chain.project,
        opencode_chain.env,
        tool="read",
        args={"file_path": OFF_LIMITS},
    )

    assert said["registered"], "no hooks were bound, so nothing could refuse"
    assert said["threw"], f"the call went through unguarded: {said}"
    assert "AI_HATS_GATE_BROKEN_ACK" in said["message"], said["message"]


def test_the_hatch_lets_a_human_past_the_vanished_manifest(opencode_chain) -> None:
    """The refusal above is only defensible because this passes."""
    opencode_chain.manifest.unlink()

    said = run_opencode_dispatch(
        opencode_chain.project,
        {**opencode_chain.env, "AI_HATS_GATE_BROKEN_ACK": "1"},
        tool="read",
        args={"file_path": OFF_LIMITS},
    )

    assert not said["threw"], said
