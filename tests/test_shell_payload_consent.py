"""The consent readers see a gated move inside a shell's ``-c`` payload.

Measured 2026-09-18: `timeout 600 ai-hats wt merge` raised the question and
`timeout 600 bash -c 'ai-hats wt merge; rc=$?; …'` raised nothing, so the merge
reached the engine, which refused with a verb only a human can type. The
destructive checks already read shell payloads (`shell_payloads`); the consent
readers did not. An interpreter's ``-c`` argument is code, not a command line,
and stays unread (HATS-1253 R5).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = (
    REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard" / "hooks"
)


def _load(name: str):
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def guard():
    sys.path.insert(0, str(HOOKS))
    try:
        _load("consent_spellings")
        yield _load("safety_gate")
    finally:
        sys.path.remove(str(HOOKS))


WRAPPED_MERGE = (
    "timeout 600 bash -c 'ai-hats wt merge task/x; rc=$?; echo $rc > /tmp/x.rc; exit $rc'"
    " > /tmp/x.log 2>&1"
)


@pytest.mark.parametrize(
    ("command", "name", "anchor", "argv"),
    [
        (WRAPPED_MERGE, "ai-hats", "timeout", ["ai-hats", "wt", "merge", "task/x"]),
        (
            "bash -c 'ai-hats wt merge task/x'",
            "ai-hats",
            "bash",
            ["ai-hats", "wt", "merge", "task/x"],
        ),
        (
            'sh -c "rack transition HATS-1 execute"',
            "rack",
            "sh",
            ["rack", "transition", "HATS-1", "execute"],
        ),
        # `-lc`: the bundle a login shell arrives in.
        (
            "bash -lc 'rack transition HATS-1 --state done'",
            "rack",
            "bash",
            ["rack", "transition", "HATS-1", "--state", "done"],
        ),
        # Two levels: the outer head is still where the ticket has to land.
        (
            "bash -c \"sh -c 'rack transition HATS-1 execute'\"",
            "rack",
            "bash",
            ["rack", "transition", "HATS-1", "execute"],
        ),
    ],
)
def test_a_gated_call_inside_a_shell_payload_is_read(guard, command, name, anchor, argv):
    found = guard.anchored_calls(command, name)

    assert [(a, o, t, list(v)) for a, o, t, v in found] == [(anchor, 0, 1, argv)]


def test_the_ticket_lands_before_the_outer_head_so_the_env_reaches_the_inner_call(guard):
    [(anchor, ordinal, total, _argv)] = guard.anchored_calls(WRAPPED_MERGE, "ai-hats")

    rewritten = guard.prefixed_command(WRAPPED_MERGE, anchor, ordinal, total, "TICKET=1")

    assert rewritten == f"TICKET=1 {WRAPPED_MERGE}"


def test_an_interpreter_payload_stays_unread(guard):
    """R5: a Python string that names a move is not a move."""
    assert (
        guard.anchored_calls("python3 -c \"print('rack transition HATS-1 execute')\"", "rack") == []
    )


def test_a_cd_inside_the_payload_makes_the_target_unknowable(guard):
    """A ticket minted for the outer cwd would be refused by a rack running
    elsewhere — the deny that names the obstacle is the honest answer."""
    assert guard.target_cwd("bash -c 'cd /elsewhere && rack transition HATS-1 execute'") is None


def test_a_lookup_change_inside_the_payload_is_seen(guard):
    assert guard._changes_command_lookup(
        "bash -c 'PATH=/tmp:$PATH rack transition HATS-1 execute'", "rack"
    )


def test_a_module_spelling_inside_the_payload_is_seen(guard):
    assert guard._python_module_calls(
        "bash -c 'python3 -m ai_hats_rack transition HATS-1 execute'", "rack"
    ) == [["transition", "HATS-1", "execute"]]
