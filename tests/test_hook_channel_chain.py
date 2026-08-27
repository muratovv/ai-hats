"""``run_chain`` against real hook scripts — the execution half of the channel.

Real scripts, not a patched spawner: the contract under test IS what a child
process does with a budget, a stdin payload and an exit code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai_hats.surfaces import profiles
from ai_hats.surfaces.hook_channel import (
    GATE_BROKEN_ACK_ENV,
    HOOK_TIMEOUT_ENV,
    ChainDecision,
    HookEvent,
    HookRow,
    resolve_hook_timeout,
    run_chain,
    surface_timeout,
)

_PROFILE = profiles.CODEX
_BASH = {"tool_name": "Bash", "tool_input": {"command": "true"}}


def _hook(tmp_path: Path, name: str, body: str) -> HookRow:
    script = tmp_path / name
    script.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return HookRow(command=script, matcher="Bash", tag=f"ai-hats:{name}")


def _emit(**hook_specific) -> str:
    payload = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **hook_specific}})
    return f"cat >/dev/null; printf '%s' {json.dumps(payload)}"


def _run(tmp_path: Path, *rows: HookRow, **kwargs):
    return run_chain(
        _PROFILE,
        event=HookEvent.PRE_TOOL_USE,
        rows=list(rows),
        payloads=[_BASH],
        project_dir=tmp_path,
        **kwargs,
    )


def test_a_silent_chain_allows(tmp_path: Path) -> None:
    verdict = _run(tmp_path, _hook(tmp_path, "quiet", "cat >/dev/null; exit 0"))
    assert verdict.decision is ChainDecision.ALLOW
    assert verdict.hatch_env == ""


def test_a_refusal_ends_the_chain_and_names_its_author(tmp_path: Path) -> None:
    denied = _hook(
        tmp_path, "deny", _emit(permissionDecision="deny", permissionDecisionReason="no")
    )
    later = _hook(tmp_path, "later", "cat >/dev/null; touch " + str(tmp_path / "ran"))
    verdict = _run(tmp_path, denied, later)
    assert verdict.decision is ChainDecision.DENY
    assert verdict.reason == "no"
    assert verdict.hook == "ai-hats:deny"
    assert not (tmp_path / "ran").exists(), "the chain kept running past a refusal"


def test_a_hooks_own_refusal_carries_no_hatch_of_ours(tmp_path: Path) -> None:
    """Arguing with it is between its author and whoever it stopped."""
    denied = _hook(
        tmp_path, "deny", _emit(permissionDecision="deny", permissionDecisionReason="no")
    )
    assert _run(tmp_path, denied).hatch_env == ""


def test_a_later_hook_still_decides_when_an_earlier_one_allows(tmp_path: Path) -> None:
    """The single-hook blind spot: hook B overriding hook A is the whole point."""
    allowed = _hook(tmp_path, "allow", _emit(permissionDecision="allow"))
    denied = _hook(tmp_path, "deny", _emit(permissionDecision="deny", permissionDecisionReason="B"))
    verdict = _run(tmp_path, allowed, denied)
    assert (verdict.decision, verdict.reason) == (ChainDecision.DENY, "B")


def test_nudges_from_several_hooks_all_survive_with_their_authors(tmp_path: Path) -> None:
    first = _hook(tmp_path, "one", _emit(additionalContext="prefer Grep"))
    second = _hook(tmp_path, "two", _emit(additionalContext="mind the budget"))
    verdict = _run(tmp_path, first, second)
    assert [(n.text, n.hook) for n in verdict.nudges] == [
        ("prefer Grep", "ai-hats:one"),
        ("mind the budget", "ai-hats:two"),
    ]


def test_a_nudge_gathered_before_a_refusal_is_not_lost(tmp_path: Path) -> None:
    nudged = _hook(tmp_path, "nudge", _emit(additionalContext="heads up"))
    denied = _hook(
        tmp_path, "deny", _emit(permissionDecision="deny", permissionDecisionReason="no")
    )
    verdict = _run(tmp_path, nudged, denied)
    assert verdict.decision is ChainDecision.DENY
    assert [n.text for n in verdict.nudges] == ["heads up"]


def test_exit_two_refuses_with_the_case_it_made_on_stderr(tmp_path: Path) -> None:
    hard = _hook(tmp_path, "hard", "cat >/dev/null; echo 'BLOCKED: shared state' >&2; exit 2")
    verdict = _run(tmp_path, hard)
    assert verdict.decision is ChainDecision.DENY
    assert "BLOCKED: shared state" in verdict.reason


def test_a_missing_script_refuses_and_names_the_way_past(tmp_path: Path) -> None:
    absent = HookRow(command=tmp_path / "gone", matcher="Bash", tag="ai-hats:gone")
    verdict = _run(tmp_path, absent)
    assert verdict.decision is ChainDecision.DENY
    assert verdict.hatch_env == GATE_BROKEN_ACK_ENV


def test_a_script_that_is_not_executable_refuses_too(tmp_path: Path) -> None:
    """Deleting the +x bit must not become a way to disarm a gate quietly."""
    script = tmp_path / "inert"
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    script.chmod(0o644)
    row = HookRow(command=script, matcher="Bash", tag="ai-hats:inert")
    assert _run(tmp_path, row).decision is ChainDecision.DENY


def test_a_hook_that_overruns_its_budget_refuses_and_names_the_bound(tmp_path: Path) -> None:
    slow = _hook(tmp_path, "slow", "cat >/dev/null; sleep 5")
    verdict = _run(tmp_path, slow, environ={HOOK_TIMEOUT_ENV: "0.4"})
    assert verdict.decision is ChainDecision.DENY
    assert verdict.hatch_env == HOOK_TIMEOUT_ENV


def test_one_slow_hook_cannot_let_the_rest_pass_unexamined(tmp_path: Path) -> None:
    """The chain's budget is one budget: with none left, the rest refuse."""
    slow = _hook(tmp_path, "slow", "cat >/dev/null; sleep 5")
    later = _hook(tmp_path, "later", "cat >/dev/null; exit 0")
    verdict = _run(tmp_path, slow, later, environ={HOOK_TIMEOUT_ENV: "0.4"})
    assert verdict.decision is ChainDecision.DENY


def test_an_unreadable_answer_refuses_rather_than_reading_as_silence(tmp_path: Path) -> None:
    noise = _hook(tmp_path, "noise", "cat >/dev/null; printf '%s' '{not json'")
    verdict = _run(tmp_path, noise)
    assert verdict.decision is ChainDecision.DENY
    assert verdict.hatch_env == GATE_BROKEN_ACK_ENV


def test_a_reply_far_larger_than_the_old_tail_still_parses(tmp_path: Path) -> None:
    """`py_security_lint` emits ruff's whole output; measured over 11 KB here,
    against a 4 KB tail that cuts a JSON document's head off."""
    big = "x" * 20_000
    row = _hook(tmp_path, "big", _emit(additionalContext=big))
    verdict = _run(tmp_path, row)
    assert verdict.decision is ChainDecision.ALLOW
    assert verdict.nudges and verdict.nudges[0].text == big


def test_the_hook_is_handed_the_payload_on_stdin(tmp_path: Path) -> None:
    seen = tmp_path / "seen.json"
    row = _hook(tmp_path, "echo", f"cat > {seen}")
    _run(tmp_path, row)
    assert json.loads(seen.read_text(encoding="utf-8"))["tool_name"] == "Bash"


def test_a_row_whose_matcher_misses_never_runs(tmp_path: Path) -> None:
    ran = tmp_path / "ran"
    row = _hook(tmp_path, "edits", f"cat >/dev/null; touch {ran}")
    verdict = run_chain(
        _PROFILE,
        event=HookEvent.PRE_TOOL_USE,
        rows=[HookRow(command=row.command, matcher="Edit|Write", tag=row.tag)],
        payloads=[_BASH],
        project_dir=tmp_path,
    )
    assert verdict.decision is ChainDecision.ALLOW
    assert not ran.exists()


class TestBudgetResolution:
    def test_the_default_holds_when_nothing_overrides_it(self) -> None:
        assert resolve_hook_timeout({}) > 0

    @pytest.mark.parametrize("bad", ["", "abc", "0", "-3"])
    def test_an_unusable_override_never_disarms_the_bound(self, bad: str) -> None:
        assert resolve_hook_timeout({HOOK_TIMEOUT_ENV: bad}) == resolve_hook_timeout({})

    def test_a_usable_override_is_taken(self) -> None:
        assert resolve_hook_timeout({HOOK_TIMEOUT_ENV: "12.5"}) == 12.5

    def test_the_surface_is_always_given_more_room_than_the_chain(self) -> None:
        """Equal bounds are what made codex's every timeout branch unreachable."""
        for environ in ({}, {HOOK_TIMEOUT_ENV: "12.5"}, {HOOK_TIMEOUT_ENV: "600"}):
            assert surface_timeout(environ) > resolve_hook_timeout(environ)

    def test_it_reads_the_real_environment_when_handed_none(self) -> None:
        os.environ[HOOK_TIMEOUT_ENV] = "7"
        try:
            assert resolve_hook_timeout() == 7
        finally:
            del os.environ[HOOK_TIMEOUT_ENV]
