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
        # The envelope IS how a session reaches the dispatcher since HATS-1594, and
        # the e2e conftest scrubs it from os.environ — so it has to be planted here
        # or the dispatcher exits early as a standalone agy run and nothing fires.
        "AI_HATS_SESSION_IDENTITY": json.dumps({"id": SESSION_ID, "project_dir": str(project)}),
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


def test_a_reclaimed_cache_dir_refuses_the_call_it_can_no_longer_guard(agy_chain) -> None:
    """HATS-1339: a sweep deleted a live session's cache dir and the guard above
    stopped refusing in total silence — exit 0, empty stderr, nothing to notice.

    That card chose to report and keep going; the reversal is deliberate, and is
    only defensible because the hatch it names now opens (see the test below).
    Fail-under-revert: without the manifest check the run is byte-identical to a
    session with no hooks, and the call the guard exists to stop goes through.
    """
    agy_chain.manifest.unlink()

    done = run_agy_dispatch(
        agy_chain.project,
        agy_chain.env,
        tool=GUARDED_TOOL,
        tool_input={"file_path": OFF_LIMITS},
    )

    spoken = json.loads(done.stdout)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny", done.stdout
    assert "no hooks manifest at" in spoken["permissionDecisionReason"]
    assert str(agy_chain.manifest) in spoken["permissionDecisionReason"]
    assert "AI_HATS_GATE_BROKEN_ACK" in spoken["permissionDecisionReason"], (
        f"the refusal names no way past it:\n{done.stdout!r}"
    )
    assert _fired(agy_chain) == [], (
        f"hooks ran for a call that was refused; fired: {_fired(agy_chain)}"
    )


def test_the_hatch_that_refusal_names_lets_the_human_through(agy_chain) -> None:
    """The other half of the reversal, and the reason it is allowed to be one.

    A refusal on every tool call with no working way past it wedges the session
    exactly as HATS-1339 feared. This is the test that says it does not.

    ``_fired`` is the load-bearing assertion, not the exit code: an opened hatch
    means the call GOES AHEAD, so the user's own hooks — a channel that is not
    ai-hats' composition — must run for it. HATS-1339's test guarded that with
    "losing one channel must not disarm both", and the refusal path is the only
    half of that which stopped being true.
    """
    agy_chain.manifest.unlink()

    done = run_agy_dispatch(
        agy_chain.project,
        {**agy_chain.env, "AI_HATS_GATE_BROKEN_ACK": "1"},
        tool=GUARDED_TOOL,
        tool_input={"file_path": OFF_LIMITS},
    )

    assert done.returncode == 0, done.stderr
    assert "permissionDecision" not in done.stdout, done.stdout
    assert "SKIPPED" in done.stderr, (
        f"the hatch was taken in silence — an unrecorded bypass:\n{done.stderr!r}"
    )
    assert _fired(agy_chain) == ["user"], (
        f"the hatch let the CALL through and disarmed the user's own channel "
        f"with it — both, where only ai-hats' own was meant to open; "
        f"fired: {_fired(agy_chain)}"
    )


def test_the_hatch_on_a_single_broken_gate_leaves_both_channels_alone(agy_chain) -> None:
    """The other level a gate can fail to be delivered: the manifest resolved
    and one script in it did not.

    Same question, and it was already answered right — the chain skips that row
    and carries on, so the call goes ahead and the user's hooks run for it. Kept
    because the manifest-level path got this wrong while this one did not, and
    nothing said which was which.
    """
    agy_chain.manifest.write_text(
        json.dumps(
            {
                "PreToolUse": [
                    {"matcher": GUARDED_TOOL, "command": str(agy_chain.project / "gone.sh")}
                ]
            }
        )
    )

    done = run_agy_dispatch(
        agy_chain.project,
        {**agy_chain.env, "AI_HATS_GATE_BROKEN_ACK": "1"},
        tool=GUARDED_TOOL,
        tool_input={"file_path": OFF_LIMITS},
    )

    assert done.returncode == 0, done.stderr
    assert "SKIPPED" in done.stderr, done.stderr
    assert _fired(agy_chain) == ["user"], (
        f"the hatch opened ai-hats' gate and closed the user's; fired: {_fired(agy_chain)}"
    )
