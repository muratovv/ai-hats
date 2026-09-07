"""e2e (HATS-1897)

flow: the MCP client approves a real rack transition through the session wrapper
cmds:
    rack transition HATS-1897 execute
expect: no task or worktree mutation before Accept; the wrapper then runs rack
why: a successful form alone does not prove the command boundary
"""

from __future__ import annotations

import json
import select
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.codex_consent import session

pytestmark = pytest.mark.integration
TASK = "HATS-1897"


def rack(project: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class Rpc:
    def __init__(self, process, project, env):
        self.process, self.project, self.env = process, project, env

    def send(self, **message):
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.process.stdin.flush()

    def receive(self):
        assert select.select([self.process.stdout], [], [], 45)[0], "MCP response timed out"
        line = self.process.stdout.readline()
        assert line, "MCP server closed stdout"
        message = json.loads(line)
        return message if "id" in message else self.receive()

    def initialize(self, capabilities=None):
        self.send(
            id=1,
            method="initialize",
            params={
                "protocolVersion": "2025-06-18",
                "capabilities": {"elicitation": {"form": {}}}
                if capabilities is None
                else capabilities,
                "clientInfo": {"name": "consent-test", "version": "1"},
            },
        )
        assert "result" in self.receive()
        self.send(method="notifications/initialized")

    def action(self, args=None, call_id=2):
        self.send(
            id=call_id,
            method="tools/call",
            params={
                "name": "rack_transition",
                "arguments": {"args": [TASK, "execute"] if args is None else args},
            },
        )
        return self.receive()

    def state(self):
        result = rack(self.project, self.env, "context", TASK, "--json")
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)["task"]["state"]


@pytest.fixture
def rpc(tmp_path, monkeypatch):
    project, env = session(tmp_path, monkeypatch)
    for args in (("create", "Consent probe", "--id", TASK), ("transition", TASK, "plan")):
        result = rack(project, env, *args)
        assert result.returncode == 0, result.stderr
    plan = project / ".agent/ai-hats/tracker/backlog/tasks" / TASK / "plan.md"
    plan.write_text(
        "\n".join(
            f"## {name}\nTest requirement."
            for name in (
                "Requirements",
                "Approach & counter",
                "Scope & Out-of-scope",
                "Steps",
                "Verification Protocol",
            )
        )
    )
    with (tmp_path / "server.stderr").open("w+") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", "ai_hats.surfaces.codex.consent_server"],
            cwd=project,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
        )
        try:
            yield Rpc(process, project, env)
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            stderr.seek(0)
            print(stderr.read())


def test_accept_runs_real_transition_only_after_answer(rpc):
    rpc.initialize()
    question = rpc.action()
    assert question["method"] == "elicitation/create", question
    assert "rack transition HATS-1897 execute" in question["params"]["message"]
    assert question["params"]["requestedSchema"] == {"type": "object", "properties": {}}
    assert rpc.state() == "plan"

    rpc.send(id=question["id"], result={"action": "accept", "content": {}})
    answer = rpc.receive()["result"]["structuredContent"]

    assert answer["decision"] == "accept", answer
    assert answer["execution"] == "finished", answer
    assert answer["exit_code"] == 0, answer
    assert rpc.state() == "execute"
    worktree = next(line for line in answer["output"].splitlines() if "Worktree:" in line)
    assert Path(worktree.split("Worktree:", 1)[1].strip()).is_dir()
    entries = [
        json.loads(line)
        for line in (rpc.project / ".git/ai-hats/bypasses.jsonl").read_text().splitlines()
    ]
    uses = [
        entry
        for entry in entries
        if entry["hook"] == "consent_wrapper.py" and answer["request_id"] in entry["reason"]
    ]
    assert len(uses) == 1


@pytest.mark.parametrize("action", ["decline", "cancel"])
def test_refusal_does_not_authorize_the_next_request(rpc, action):
    rpc.initialize()
    question = rpc.action()
    rpc.send(id=question["id"], result={"action": action})
    answer = rpc.receive()["result"]["structuredContent"]
    assert answer["decision"] == action
    assert answer["execution"] == "not_started"
    assert rpc.state() == "plan"
    assert not list((rpc.project / ".git/ai-hats/consent").glob("*.json"))

    next_question = rpc.action(call_id=3)
    assert next_question["method"] == "elicitation/create"
    assert next_question["params"]["message"] != question["params"]["message"]
    rpc.send(id=next_question["id"], result={"action": "accept", "content": {}})
    next_result = rpc.receive()["result"]["structuredContent"]
    assert next_result["exit_code"] == 0, next_result
    assert rpc.state() == "execute"


@pytest.mark.parametrize(
    "reply",
    [
        {"action": "accept", "content": None},
        {"action": "accept", "content": {"substitute": True}},
        {"action": "invalid"},
    ],
)
def test_invalid_form_response_never_starts_command(rpc, reply):
    rpc.initialize()
    question = rpc.action()
    rpc.send(id=question["id"], result=reply)
    answer = rpc.receive()["result"]["structuredContent"]
    assert answer["execution"] == "not_started", answer
    assert answer["decision"] == "error", answer
    assert rpc.state() == "plan"
    assert not list((rpc.project / ".git/ai-hats/consent").glob("*.json"))


def test_changed_state_invalidates_answer_without_holding_task_lock(rpc):
    rpc.initialize()
    question = rpc.action()
    moved = rack(rpc.project, rpc.env, "transition", TASK, "blocked")
    assert moved.returncode == 0, moved.stderr
    rpc.send(id=question["id"], result={"action": "accept", "content": {}})
    answer = rpc.receive()["result"]["structuredContent"]
    assert answer["decision"] == "accept"
    assert answer["execution"] == "not_started"
    assert "stale_request" in answer["output"]
    assert rpc.state() == "blocked"
    assert not list((rpc.project / ".git/ai-hats/consent").glob("*.json"))


def test_force_still_obeys_real_plan_quality_gate(rpc):
    removed = rack(rpc.project, rpc.env, "transition", TASK, "--rm", "plan.md")
    assert removed.returncode == 0, removed.stderr
    rpc.initialize()
    question = rpc.action([TASK, "execute", "--force", "--reason", "explicit probe"])
    assert question["method"] == "elicitation/create", question
    rpc.send(id=question["id"], result={"action": "accept", "content": {}})
    answer = rpc.receive()["result"]["structuredContent"]
    assert answer["decision"] == "accept"
    assert answer["execution"] == "finished"
    assert answer["exit_code"] != 0
    assert "plan" in answer["output"].lower(), answer
    assert rpc.state() == "plan"


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--help"],
        [TASK, "execute", "--state", "done"],
        [TASK, "execute", "--tasks-dir", "/tmp/foreign-project"],
        [TASK, "execute", "--log", "bad\x00value"],
    ],
)
def test_invalid_argv_is_refused_before_elicitation(rpc, args):
    rpc.initialize()
    answer = rpc.action(args)["result"]["structuredContent"]
    assert answer["execution"] == "not_started"
    assert answer["decision"] == "error"
    assert rpc.state() == "plan"


def test_done_approval_covers_nested_worktree_merge(rpc):
    from _helpers.git import git

    rpc.initialize()
    question = rpc.action()
    rpc.send(id=question["id"], result={"action": "accept", "content": {}})
    started = rpc.receive()["result"]["structuredContent"]
    assert started["exit_code"] == 0, started
    line = next(line for line in started["output"].splitlines() if "Worktree:" in line)
    worktree = Path(line.split("Worktree:", 1)[1].strip())
    (worktree / "approved.txt").write_text("approved merge\n")
    git(worktree, "add", "approved.txt")
    git(worktree, "-c", "core.hooksPath=/dev/null", "commit", "-m", "test: prepare merge")
    for state in ("document", "review"):
        moved = rack(rpc.project, rpc.env, "transition", TASK, state)
        assert moved.returncode == 0, moved.stderr

    question = rpc.action([TASK, "done"], call_id=3)
    assert question["method"] == "elicitation/create", question
    rpc.send(id=question["id"], result={"action": "accept", "content": {}})
    merged = rpc.receive()["result"]["structuredContent"]
    assert merged["exit_code"] == 0, merged["output"]
    assert rpc.state() == "done"
    assert (rpc.project / "approved.txt").read_text() == "approved merge\n"
    assert not worktree.exists()


def test_client_without_elicitation_cannot_authorize(rpc):
    rpc.initialize(capabilities={})
    answer = rpc.action()["result"]["structuredContent"]
    assert answer["execution"] == "not_started"
    assert "does not support" in answer["output"]
    assert rpc.state() == "plan"


def test_existing_human_grant_is_respected(rpc):
    issued = subprocess.run(
        ["consent", "rack.transition", "30"],
        cwd=rpc.project,
        env=rpc.env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert issued.returncode == 0, issued.stderr
    rpc.initialize()
    answer = rpc.action()["result"]["structuredContent"]
    assert answer["decision"] == "covered", answer
    assert answer["exit_code"] == 0, answer
    assert rpc.state() == "execute"


def test_disconnected_client_leaves_no_authorization(rpc):
    rpc.initialize()
    assert rpc.action()["method"] == "elicitation/create"
    rpc.process.stdin.close()
    rpc.process.wait(timeout=10)
    assert rpc.state() == "plan"
    assert not list((rpc.project / ".git/ai-hats/consent").glob("*.json"))


def test_journal_failure_blocks_accepted_request_before_ticket(rpc):
    rpc.initialize()
    question = rpc.action()
    journal = rpc.project / ".git/ai-hats/bypasses.jsonl"
    journal.mkdir(parents=True)
    rpc.send(id=question["id"], result={"action": "accept", "content": {}})
    answer = rpc.receive()["result"]["structuredContent"]
    assert answer["execution"] == "not_started", answer
    assert "journal" in answer["output"]
    assert rpc.state() == "plan"
    assert not list((rpc.project / ".git/ai-hats/consent").glob("*.json"))


def test_concurrent_questions_keep_their_own_answers(rpc):
    rpc.initialize()
    first = rpc.action(call_id=2)
    second = rpc.action(call_id=3)
    assert first["method"] == second["method"] == "elicitation/create"
    assert first["id"] != second["id"]
    rpc.send(id=first["id"], result={"action": "decline"})
    refused = rpc.receive()
    assert refused["id"] == 2
    assert refused["result"]["structuredContent"]["execution"] == "not_started"
    rpc.send(id=second["id"], result={"action": "accept", "content": {}})
    accepted = rpc.receive()
    assert accepted["id"] == 3
    assert accepted["result"]["structuredContent"]["exit_code"] == 0
    assert rpc.state() == "execute"
