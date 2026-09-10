"""Behavioural tests for the self-granted-approval PreToolUse guard (HATS-1944).

The guard's whole value is a distinction: a flag a line SETS versus the same text
sitting in argument position. A guard that cannot tell them apart refuses `grep`
for its own flag name — which is how an agent learns to route around it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.constants import (
    BYPASS_FLAG_SUFFIXES,
    BYPASS_FLAGS_NOT_INHERITED,
    BYPASS_FLAGS_OFF_CONVENTION,
    CONSENT_OWNED_KEYS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = (
    REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard/hooks"
)
GUARD = HOOKS_DIR / "ack_prefix_guard.py"

#: Assembled rather than spelled: a literal consent flag on a command line is itself
#: refused by the consent gate, so a test that spelled one could not even be run.
_CONSENT_FLAG = "AI_HATS_" + "PLAN" + "_ACK"


def _run(command: str) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def _denied(command: str) -> bool:
    res = _run(command)
    assert res.returncode == 0, f"a PreToolUse hook always exits 0: {res.stderr}"
    if not res.stdout.strip():
        return False
    decision = json.loads(res.stdout)["hookSpecificOutput"]
    return decision["permissionDecision"] == "deny"


# --------------------------------------------------------------------------- deny


@pytest.mark.parametrize(
    ("label", "command"),
    [
        ("the plain prefix", "AI_HATS_RED_MASTER_ACK=1 make done-gate"),
        ("the smoke skip of HATS-1324", "AI_HATS_SMOKE_SKIP=1 git commit -m x"),
        ("a bare assignment, binding the rest of the line", "AI_HATS_RED_MASTER_ACK=1; make x"),
        ("export", "export AI_HATS_RED_MASTER_ACK=1"),
        ("the env form", "env AI_HATS_WT_GATE_OFF=1 pytest"),
        ("after a separator", "make lint && AI_HATS_RED_MASTER_ACK=1 make done-gate"),
        ("with the space deleted", "cd /x;AI_HATS_YOLO=1 make done-gate"),
        ("an off-convention name", "AI_HATS_SKIP_SELF_LOCATION_GUARD=1 ai-hats self init"),
        ("a flag this repo has not invented yet", "AI_HATS_FUTURE_GATE_ACK=1 make done-gate"),
    ],
)
def test_a_line_that_grants_itself_an_approval_is_refused(label: str, command: str) -> None:
    assert _denied(command), f"not refused: {label}"


def test_the_refusal_says_where_the_flag_must_come_from_instead() -> None:
    """A deny that names no other road is the one an agent works around."""
    reason = json.loads(_run("AI_HATS_RED_MASTER_ACK=1 make done-gate").stdout)[
        "hookSpecificOutput"
    ]["permissionDecisionReason"]
    assert "AI_HATS_RED_MASTER_ACK" in reason, "the reason must name what it caught"
    assert "LAUNCHES" in reason, "the reason must name the environment that can carry it"
    assert "red-attribution" in reason, "the reason must route to the procedure"


# -------------------------------------------------------------------------- allow


@pytest.mark.parametrize(
    ("label", "command"),
    [
        ("the flag quoted as an argument", 'echo "AI_HATS_RED_MASTER_ACK=1 make done-gate"'),
        ("a grep for the flag's own name", "grep -rn AI_HATS_RED_MASTER_ACK scripts/"),
        ("the gate, run honestly", "make done-gate"),
        ("an unrelated inline assignment", "PATH=/x:$PATH pytest"),
        ("a non-ai-hats name that ends in the suffix", "SOME_OTHER_ACK=1 make done-gate"),
        ("an ai-hats var that approves nothing", "AI_HATS_DEBUG=1 ai-hats config show"),
    ],
)
def test_a_line_that_grants_nothing_is_untouched(label: str, command: str) -> None:
    assert not _denied(command), f"wrongly refused: {label}"


def test_consent_flags_are_left_to_the_consent_gate() -> None:
    """Two gates on one concern means the coarser one silently wins (safety-guard)."""
    assert not _denied(f"{_CONSENT_FLAG}=1 rack transition X execute")


# ----------------------------------------------------------------------- contract


def test_the_guard_mirrors_the_conventions_it_cannot_import() -> None:
    """Hooks are stdlib-only, so the shape is copied — and drift is caught here."""
    sys.path.insert(0, str(HOOKS_DIR))
    try:
        import ack_prefix_guard as guard
    finally:
        sys.path.pop(0)

    assert guard.BYPASS_FLAG_SUFFIXES == BYPASS_FLAG_SUFFIXES
    assert guard.BYPASS_FLAGS_OFF_CONVENTION == BYPASS_FLAGS_OFF_CONVENTION
    assert guard.CONSENT_OWNED_KEYS == CONSENT_OWNED_KEYS


def test_every_withheld_flag_is_also_refused_inline() -> None:
    """The withholding roster and this guard must not disagree about one name.

    A flag worth blanking for a sub-agent is worth refusing on the parent's own
    command line; the two answering differently is the gap this card closed."""
    sys.path.insert(0, str(HOOKS_DIR))
    try:
        import ack_prefix_guard as guard
    finally:
        sys.path.pop(0)

    missed = [name for name in BYPASS_FLAGS_NOT_INHERITED if not guard.is_self_grantable(name)]
    assert missed == [], f"withheld from a sub-agent but self-grantable inline: {missed}"
