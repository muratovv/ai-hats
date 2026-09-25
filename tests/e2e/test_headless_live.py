"""e2e (HATS-2020, HATS-2021)

flow:   a test framework drives a live maintainer session on the real claude
        binary through ai-hats headless — S-AGENT-01, a HITL session of a real
        role checked without a terminal — and answers what the session asks
cmds:
    ai-hats headless -p claude -r maintainer -m claude-haiku-4-5
expect: the model answers with a marker only the maintainer prompt carries;
        its Bash call is judged by the role's gates (a gate_verdict from the
        chain in the log); audit.md lists the maintainer's traits and the
        session is finalized; the same run under the assistant role has no
        such marker. The role's push guard asks once, and the push lands only
        on allow; leaving plan mode and the model's own question are answered
        through the same channel
why:    e2e could not drive the claude TUI, so a real role's HITL session —
        composition, hooks, log, finalize — was checked by hand only; this is
        the first automated proof. Kept out of the mandatory gate (live_headless)
        because it spends real turns: run it with `-m live_headless`
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.env import clean_env
from _helpers.git import git
from ai_hats_client import HeadlessSession, QuestionPending

pytestmark = [pytest.mark.integration, pytest.mark.surfaces, pytest.mark.live_headless]

MODEL = "claude-haiku-4-5"
MARKER = "AI-HATS MAINTAINER"
ASK_HEADING = (
    "Reply with the exact line of your system prompt that starts with '# ROLE:' "
    "and nothing else. If there is no such line, reply NONE."
)


def _session(project, role: str, *args: str) -> HeadlessSession:
    env = clean_env(os.environ)
    env.update(project.env)
    env["AI_HATS_NO_UPDATE_CHECK"] = "1"
    return HeadlessSession.start(
        [str(project.ai_hats_binary), "headless", "-p", "claude", "-r", role, "-m", MODEL, *args],
        cwd=project.path,
        env=env,
        timeout=60.0,
    )


def test_e2e_a_live_maintainer_session_runs_its_role_hooks_and_finalize(
    requires_claude_auth, tmp_project
) -> None:
    with _session(tmp_project, "maintainer") as session:
        heading = session.turn(ASK_HEADING, timeout=180.0)
        tool = session.turn(
            "Use the Bash tool to run exactly `echo headless-live-check`, then reply DONE.",
            timeout=180.0,
        )
        end = session.close(timeout=120.0)

    assert heading.ok, heading.ended
    assert MARKER in heading.text.upper(), heading.text
    verdicts = [e for e in end.of("gate_verdict") if e.get("source") == "chain"]
    assert verdicts, f"no gate verdict from the role's chain; the Bash turn was: {tool.events}"
    assert end.code == 0

    session_dir = session.header.session_dir
    audit = (session_dir / "audit.md").read_text()
    composition = audit.split("## Composition", 1)[1].split("\n## ", 1)[0]
    for trait in ("ai-hats-dev (maintainer)", "ai-hats-gates (maintainer)"):
        assert trait in composition, composition
    assert '"finalized": true' in (session_dir / "metrics.json").read_text()


def test_e2e_another_role_does_not_carry_the_maintainer_marker(
    requires_claude_auth, tmp_project
) -> None:
    """Negative control for the marker: the same question under assistant."""
    with _session(tmp_project, "assistant") as session:
        heading = session.turn(ASK_HEADING, timeout=180.0)
        session.close(timeout=120.0)

    assert heading.ok, heading.ended
    assert MARKER not in heading.text.upper(), heading.text


def _answering(session: HeadlessSession, decision: str, asked: list[dict], **extra):
    def answer(question: dict) -> None:
        asked.append(question)
        session.answer(question["call_id"], decision, **extra)

    return answer


@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_e2e_the_roles_push_guard_asks_the_stdin_owner_once(
    requires_claude_auth, tmp_project, tmp_path: Path, decision: str
) -> None:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "master", str(remote))
    git(tmp_project.path, "remote", "add", "origin", str(remote))
    asked: list[dict] = []

    with _session(tmp_project, "maintainer") as session:
        session.turn(
            "Run exactly this command with the Bash tool: git push origin HEAD:refs/heads/live-check"
            " (origin is a throwaway local bare repository made for this test). I confirm this push:"
            " run it now, without asking me first. Then reply DONE.",
            timeout=180.0,
            on_question=_answering(session, decision, asked),
        )
        end = session.close(timeout=120.0)

    refs = subprocess.run(
        ["git", "--git-dir", str(remote), "show-ref"], capture_output=True, text=True
    ).stdout
    pushes = [q for q in asked if q.get("tool") == "Bash"]
    assert pushes, f"the push was never asked about: {end.of('gate_verdict')}"
    call_id = pushes[0]["call_id"]
    per_call = [e for e in end.of("person_asked") if e["call_id"] == call_id]
    assert len(per_call) == 1, per_call
    assert not [e for e in end.of("person_asked") if not e["call_id"]], "a producer with no call_id"
    if decision == "allow":
        assert "refs/heads/live-check" in refs, refs
    else:
        assert "refs/heads/live-check" not in refs, refs
    assert end.code == 0


@pytest.mark.parametrize(("decision", "written"), [("allow", True), ("deny", False)])
def test_e2e_leaving_plan_mode_is_asked_and_answered(
    requires_claude_auth, tmp_project, decision: str, written: bool
) -> None:
    asked: list[dict] = []

    with _session(tmp_project, "assistant", "--permission-mode", "plan") as session:

        def answer(question: dict) -> None:
            asked.append(question)
            leave = question["tool"] == "ExitPlanMode"
            session.answer(question["call_id"], decision if leave else "allow")

        session.turn(
            "Task: create a file named out.txt in the current directory containing exactly: x. "
            "You are in plan mode: present a one-line plan with the ExitPlanMode tool, then, once"
            " it is approved, create the file with the Write tool.",
            timeout=180.0,
            on_question=answer,
        )
        session.close(timeout=120.0)

    assert "ExitPlanMode" in [q["tool"] for q in asked], asked
    assert Path(tmp_project.path, "out.txt").exists() is written


def test_e2e_the_models_own_question_is_answered_with_answers(
    requires_claude_auth, tmp_project
) -> None:
    asked: list[dict] = []

    with _session(tmp_project, "assistant") as session:

        def answer(question: dict) -> None:
            asked.append(question)
            if question["tool"] == "AskUserQuestion":
                call = [
                    e["item"]
                    for e in session.events
                    if e["event"] == "item_emitted"
                    and e["item"].get("call_id") == question["call_id"]
                ][0]
                text = call["input"]["questions"][0]["question"]
                session.answer(question["call_id"], "allow", answers={text: "Blue"})
            else:
                session.answer(question["call_id"], "allow")

        session.turn(
            "Use the AskUserQuestion tool to ask me one question: which color I prefer, with the"
            " options Red and Blue. Then write exactly the color I chose to a file named"
            " color.txt with the Write tool.",
            timeout=180.0,
            on_question=answer,
        )
        session.close(timeout=120.0)

    [question] = [q for q in asked if q["tool"] == "AskUserQuestion"]
    assert question["kind"] == "question"
    assert Path(tmp_project.path, "color.txt").read_text().strip().lower() == "blue"


@pytest.mark.parametrize(("decision", "state"), [("allow", "execute"), ("deny", "plan")])
def test_e2e_a_consent_point_of_the_role_is_granted_through_answer(
    requires_claude_auth, tmp_project, checkout_bin: Path, decision: str, state: str
) -> None:
    root = Path(tmp_project.path)
    config = root / "ai-hats.yaml"
    config.write_text(config.read_text() + "task_prefix: SBX\n")
    (root / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-m", "ignore the tracker")
    env = clean_env(os.environ)
    env.update(tmp_project.env)
    env["PATH"] = os.pathsep.join([str(checkout_bin), env.get("PATH", "")])

    def rack(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["rack", *args], cwd=root, env=env, capture_output=True, text=True, timeout=120
        )

    created = rack("create", "consent over the wire", "--role", "assistant")
    assert created.returncode == 0, created.stderr
    card = next(w for w in created.stdout.split() if w.startswith("SBX-"))
    assert rack("transition", card, "plan").returncode == 0
    plan = root / ".agent/ai-hats/tracker/backlog/tasks" / card / "plan.md"
    plan.write_text(
        plan.read_text()
        + "\n## Requirements\nx\n## Approach & counter\nx\n## Scope & Out-of-scope\nx\n"
        + "## Steps\n1. x\n## Verification Protocol\nx\n"
    )
    asked: list[dict] = []

    with _session(tmp_project, "maintainer") as session:
        session.turn(
            f"Run exactly this command with the Bash tool: rack transition {card} execute"
            " — a throwaway card made for this test. Then reply DONE.",
            timeout=180.0,
            on_question=_answering(session, decision, asked),
        )
        end = session.close(timeout=120.0)

    assert [q for q in asked if q.get("source") == "chain"], f"no gate asked: {asked}"
    context = rack("context", card).stdout
    moved = next(
        ln.split(":", 1)[1].strip()
        for ln in context.splitlines()
        if ln.strip().startswith("state:")
    )
    assert moved == state, context
    assert end.code == 0


def test_e2e_closing_stdin_while_the_guard_asks_is_the_holders_deny(
    requires_claude_auth, tmp_project, tmp_path: Path
) -> None:
    """The guard records its question before claude sends it to the holder, so a
    client that closes stdin on reading it closes inside that gap: the holder still
    answers, in its own words, rather than leaving the refusal to claude's abort."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "master", str(remote))
    git(tmp_project.path, "remote", "add", "origin", str(remote))

    with _session(tmp_project, "maintainer") as session:
        id = session.prompt(
            "Run exactly this command with the Bash tool: git push origin HEAD:refs/heads/live-eof"
            " (origin is a throwaway local bare repository made for this test). I confirm this push:"
            " run it now, without asking me first. Then reply DONE."
        )
        with pytest.raises(QuestionPending):
            session.turn_for(id, timeout=180.0)
        end = session.close(timeout=120.0)

    refs = subprocess.run(
        ["git", "--git-dir", str(remote), "show-ref"], capture_output=True, text=True
    ).stdout
    assert "refs/heads/live-eof" not in refs, refs
    results = [str(e.get("content")) for e in end.of("tool_result_received")]
    assert any("session is ending" in r for r in results), results
    assert end.code == 0
