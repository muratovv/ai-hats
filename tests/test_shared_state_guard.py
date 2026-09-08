"""Behavioural tests for the shared-state PreToolUse guard (HATS-1294).

The guard shipped with no behavioural coverage: the existing
``test_rule_pause_before_shared_state_write.py`` only asserts the rule file
exists and composes, which is why a deny message advertising an unreachable
escape survived unnoticed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = (
    Path(__file__).resolve().parents[1]
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard/hooks"
)
GUARD = HOOKS_DIR / "pre_bash_shared_state_guard.sh"
CLASSIFIER = HOOKS_DIR / "shared_state_classifier.sh"
RULE = (
    Path(__file__).resolve().parents[1]
    / "packages/ai-hats-library/src/ai_hats_library/core/rules"
    / "rule_pause_before_shared_state_write/rule.md"
)

# The prefix form the hook used to instruct and then refuse (HATS-1294).
DEAD_ESCAPE = "AI_HATS_SHARED_STATE_ACK=1 <command>"


def _classify(command: str) -> str:
    return subprocess.run(
        [str(CLASSIFIER), command], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def _run_guard(hook_repo):
    def run(
        command: str, *, ack: bool = False, claude: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Drive the guard.

        ``claude`` selects the caller's dialect: Claude Code's PreToolUse payload
        carries ``hook_event_name``, a plain invocation (cline's surface, a CLI
        probe) does not.
        """
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
        if ack:
            env["AI_HATS_SHARED_STATE_ACK"] = "1"
        payload: dict[str, object] = {"tool_input": {"command": command}}
        if claude:
            payload["hook_event_name"] = "PreToolUse"
        return subprocess.run(
            [str(GUARD)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            cwd=hook_repo,
            timeout=20,
        )

    return run


@pytest.mark.parametrize(
    ("command", "verdict"),
    [
        # --force and its aliases
        ("git push --force origin master", "irreversible"),
        ("git push -f origin master", "irreversible"),
        ("git push --force-with-lease origin master", "irreversible"),
        # HATS-1294: other spellings of the same force, previously rated `gated`
        ("git push origin +master:master", "irreversible"),
        ("git push origin +refs/heads/master:refs/heads/master", "irreversible"),
        ("git push --mirror origin", "irreversible"),
        # Ref deletion
        ("git push origin :master", "irreversible"),
        ("git push origin --delete task/old", "irreversible"),
        ("gh pr merge 12", "irreversible"),
        # Not force: an ordinary push, and an ordinary refspec that merely
        # contains a colon, must stay `gated` — the fix must not over-match.
        ("git push origin master", "gated"),
        ("git push HEAD:refs/heads/probe", "gated"),
        ("git push --dry-run origin master", "safe"),
        ("gh pr create --fill", "shared"),
        ("gh issue comment 4 --body hi", "shared"),
        ("ls -la", "safe"),
    ],
)
def test_classifier_verdicts(command: str, verdict: str) -> None:
    """RED-under-revert: dropping the +refspec/--mirror/:branch rules fails here."""
    assert _classify(command) == verdict


def test_irreversible_command_asks_rather_than_denying(_run_guard) -> None:
    """The guard must escalate to the user, not hard-deny.

    Exit 0 matters: Claude Code discards a hook's stdout when it exits 2, so a
    guard that exits 2 can never deliver a decision payload.
    """
    result = _run_guard("git push --force origin master")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)["hookSpecificOutput"]
    assert payload["hookEventName"] == "PreToolUse"
    assert payload["permissionDecision"] == "ask"
    assert payload["permissionDecisionReason"].strip()


def test_gated_command_also_asks(_run_guard) -> None:
    result = _run_guard("git push origin master")
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
    assert decision == "ask"


def test_safe_command_passes_silently(_run_guard) -> None:
    result = _run_guard("ls -la")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_environment_ack_allows_without_prompting(_run_guard, hook_repo: Path) -> None:
    """The one channel the agent cannot reach: the launching environment."""
    result = _run_guard("git push --force origin master", ack=True)
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    journal = hook_repo / ".git/ai-hats/bypasses.jsonl"
    assert journal.is_file(), "the hook wrote outside its fixture repository"
    entry = json.loads(journal.read_text().splitlines()[-1])
    assert entry["reason"] == "AI_HATS_SHARED_STATE_ACK"


def test_non_claude_caller_gets_the_universal_deny(_run_guard) -> None:
    """The guard is not Claude-only.

    The cline surface and direct CLI probes invoke it as a plain script and
    expect ``exit 2`` with BLOCKED on stderr. Answering them with Claude's JSON
    would be an unparsed blob on stdout and a silently *allowed* command, so the
    dialect follows what the payload declares.
    """
    result = _run_guard("git push --force origin master", claude=False)
    assert result.returncode == 2
    assert "BLOCKED" in result.stderr
    assert result.stdout.strip() == "", "no JSON at a caller that cannot parse it"


def test_non_claude_caller_still_passes_safe_commands(_run_guard) -> None:
    result = _run_guard("echo hello", claude=False)
    assert result.returncode == 0


def test_reason_does_not_advertise_the_unreachable_prefix(_run_guard) -> None:
    """Anti-regression on the defect itself.

    The old message told the agent to retry as ``AI_HATS_SHARED_STATE_ACK=1
    <command>`` — a form this hook can never observe, because it runs before the
    command becomes a process. Two turns were burned on it before anyone read
    the hook source.
    """
    reason = json.loads(_run_guard("git push --force origin master").stdout)
    reason = reason["hookSpecificOutput"]["permissionDecisionReason"]
    assert DEAD_ESCAPE not in reason
    assert "environment that launched" in reason, "must name a channel that works"


@pytest.mark.parametrize("claude", [True, False])
def test_refusal_text_names_no_specific_provider(_run_guard, claude: bool) -> None:
    """This guard serves every surface, so its text must not assume one.

    Naming a provider's settings file here would be wrong under the harnesses
    that also run it.
    """
    result = _run_guard("git push --force origin master", claude=claude)
    text = result.stdout + result.stderr
    for token in (".claude/", "settings.json", "Claude Code"):
        assert token not in text, f"provider-specific token leaked: {token}"


def test_rule_names_the_dead_prefix_only_to_warn_against_it() -> None:
    """The rule *should* mention the form agents instinctively reach for.

    Deleting it silently would leave the instinct unaddressed, so the assertion
    is about framing, not absence: the string must appear, and must be marked
    non-functional, alongside a channel that does work.
    """
    body = RULE.read_text()
    assert DEAD_ESCAPE in body, "name the trap agents fall into"
    assert "does **nothing**" in body, "and mark it as non-functional"
    assert "environment that launched" in body, "point at a channel that works"
    assert "set it only on a command" not in body, "the old false guidance is gone"


def test_rule_stays_provider_agnostic() -> None:
    """This rule ships to every harness, so it must not name one.

    Reviewer's catch on the first cut: `.claude/settings.json` had leaked into a
    core rule that gemini and cline sessions read too.
    """
    body = RULE.read_text()
    for token in (".claude/", "Claude Code", "settings.json"):
        assert token not in body, f"provider-specific token leaked: {token}"
