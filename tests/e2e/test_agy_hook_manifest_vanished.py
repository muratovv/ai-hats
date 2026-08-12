"""e2e (HATS-1339)

flow:   an agent keeps editing files in an agy session whose pinned cache dir,
        hooks manifest and all, a sweep reclaimed under it
cmds:
    ai-hats agent assistant --task "Edit a file"
expect: the dispatcher names the vanished manifest on stderr and still exits 0,
        and the user's own ~/.gemini/config/hooks.json hooks keep firing
why:    a reclaimed cache dir otherwise reads as "no hooks configured" and every guard goes quiet
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from _helpers.hook_chain import run_agy_dispatch

pytestmark = pytest.mark.integration

SESSION_ID = "e2e-sid-manifest-vanished"
GUARDED_TOOL = "Edit"
OFF_LIMITS = "/etc/passwd"


def _script(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def agy_chain(tmp_path: Path) -> SimpleNamespace:
    """A pinned agy session whose PreToolUse chain is three hooks: an audit and
    a guard from the session manifest, then one from the user's own manifest."""
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    user_config = tmp_path / "home" / ".gemini" / "config"
    user_config.mkdir(parents=True)

    ledger = tmp_path / "ledger.txt"
    audit = _script(tmp_path / "audit.sh", f"#!/bin/sh\necho audit >> '{ledger}'\n")
    guard = _script(
        tmp_path / "guard.sh",
        f"#!/bin/sh\n"
        f"echo guard >> '{ledger}'\n"
        f'case "$(cat)" in\n'
        f"  *{OFF_LIMITS}*)\n"
        f"    echo 'agy-guard: {OFF_LIMITS} is off limits' >&2\n"
        f"    exit 2 ;;\n"
        f"esac\n",
    )
    user_hook = _script(tmp_path / "user.sh", f"#!/bin/sh\necho user >> '{ledger}'\n")

    manifest = cache / "hooks.json"
    manifest.write_text(
        json.dumps(
            {
                "PreToolUse": [
                    {"matcher": GUARDED_TOOL, "command": str(audit)},
                    {"matcher": GUARDED_TOOL, "command": str(guard)},
                ]
            }
        )
    )
    (user_config / "hooks.json").write_text(
        json.dumps({"PreToolUse": [{"matcher": "*", "command": str(user_hook)}]})
    )

    env = {
        **os.environ,
        "HOME": str(user_config.parents[1]),
        "AI_HATS_SESSION_ID": SESSION_ID,
        "AI_HATS_PROJECT_DIR": str(project),
        "AI_HATS_SESSION_CACHE_DIR": str(cache),
        "AI_HATS_PYTHON": sys.executable,
        # What the dispatcher matches on when agy hands it no argv.
        "AGY_TOOL_NAME": GUARDED_TOOL,
    }
    return SimpleNamespace(project=project, env=env, manifest=manifest, ledger=ledger)


def _fired(chain: SimpleNamespace) -> list[str]:
    return chain.ledger.read_text().split() if chain.ledger.is_file() else []


def test_the_pinned_manifest_drives_the_whole_chain(agy_chain) -> None:
    """The control: with the manifest there, every hook on the matcher runs, in
    order, and the user's own hook runs after the session's."""
    done = run_agy_dispatch(
        agy_chain.project,
        agy_chain.env,
        tool=GUARDED_TOOL,
        tool_input={"file_path": str(agy_chain.project / "README.md")},
    )

    assert done.returncode == 0, done.stderr
    assert _fired(agy_chain) == ["audit", "guard", "user"], done.stderr
    assert "no hooks manifest" not in done.stderr, done.stderr


def test_a_guard_further_down_the_chain_still_refuses(agy_chain) -> None:
    """The positive control for the case below: the guard is armed, and its
    refusal is what the chain reports — the first hook's PASS did not end it."""
    done = run_agy_dispatch(
        agy_chain.project,
        agy_chain.env,
        tool=GUARDED_TOOL,
        tool_input={"file_path": OFF_LIMITS},
    )

    assert done.returncode == 2, f"the guard did not refuse (exit {done.returncode})"
    assert OFF_LIMITS in done.stderr, done.stderr
    assert _fired(agy_chain) == ["audit", "guard"], "the chain did not stop at the refusal"


def test_a_reclaimed_cache_dir_is_reported_not_passed_off_as_hook_less(agy_chain) -> None:
    """HATS-1339: a sweep deleted a live session's cache dir and the guard above
    stopped refusing in total silence — exit 0, empty stderr, nothing to notice.

    Exit stays 0 on purpose (ADR-0020 D1, the detached channel is fail-open):
    this runs ahead of every tool call, so refusing would kill the very session
    the diagnostic exists to rescue. Fail-under-revert: without the manifest
    check in ``_session_hooks_file`` the run is byte-identical to a session that
    simply has no hooks, and the stderr assertions below find nothing.
    """
    agy_chain.manifest.unlink()

    done = run_agy_dispatch(
        agy_chain.project,
        agy_chain.env,
        tool=GUARDED_TOOL,
        tool_input={"file_path": OFF_LIMITS},
    )

    assert done.returncode == 0, f"fail-open broken: exit {done.returncode}\n{done.stderr}"
    assert "no hooks manifest at" in done.stderr, (
        f"the vanished manifest went unreported — indistinguishable from a "
        f"session with no hooks configured:\n{done.stderr!r}"
    )
    assert str(agy_chain.manifest) in done.stderr, done.stderr
    assert "restart it" in done.stderr, f"the report names no remedy:\n{done.stderr!r}"
    assert _fired(agy_chain) == ["user"], (
        "the user's own hooks must survive a reclaimed session cache dir; "
        f"fired: {_fired(agy_chain)}"
    )
