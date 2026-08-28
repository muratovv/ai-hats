"""e2e (HATS-1735)

flow:   a supervisor opens a window with the verb, and the agent's next moves stop asking
cmds:
    consent rack.transition 30
    rack transition SBX-001 execute
expect: the first move is a question; inside the window the composed chain stops asking,
        the card actually moves, and every use leaves a line in the bypass journal
why:    four joints can each break in silence — the envelope's cache dir, the role's
        declaration, the verb's file format, and the three call sites — so nothing here
        is fabricated: every artefact comes from the writer that ships it
"""

from __future__ import annotations

import json
import re
import os
import subprocess
import time
from pathlib import Path

import pytest

from _helpers.git import git as _git
from _helpers.hook_chain import build_session_settings, run_chain, run_unasked

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
SESSION_ID = "e2e-consent-grant"

_PLAN_SECTIONS = (
    "\n## Requirements\nx\n## Approach & counter\nx\n"
    "## Scope & Out-of-scope\nx\n## Steps\n1. x\n## Verification Protocol\nx\n"
)


def _rack(project: Path, *args: str, env: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603,S607 - session PATH selects the wrapper
        ["rack", *args],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "ai-hats.yaml").write_text("task_prefix: SBX\n")
    (root / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    (root / TASKS_SUB).mkdir(parents=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init", "--allow-empty")
    return root


@pytest.fixture
def env(project: Path, checkout_bin: Path) -> dict:
    from _helpers.env import checkout_pythonpath
    from _helpers.sessions import stand_in_wrapped_session

    e = os.environ.copy()
    for stale in ("AI_HATS_PLAN_ACK", "AI_HATS_CONSENT_ACK", "AI_HATS_CONSENT_TICKET"):
        e.pop(stale, None)
    e["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, e.get("PYTHONPATH", ""))
    e["AI_HATS_ROOT_PID"] = str(os.getpid())
    e["PATH"] = os.pathsep.join([str(checkout_bin), e.get("PATH", "")])
    return stand_in_wrapped_session(e, project, SESSION_ID)


@pytest.fixture
def settings(project: Path) -> Path:
    return build_session_settings(project)


@pytest.fixture
def planned(project: Path, env: dict):
    """Cards sitting in ``plan`` with a filled plan.md — the gate's doorstep."""

    def _make(title: str) -> str:
        created = _rack(project, "create", title, "--role", "assistant", env=env)
        assert created.returncode == 0, created.stderr
        task_id = next(w for w in created.stdout.split() if w.startswith("SBX-"))
        assert _rack(project, "transition", task_id, "plan", env=env).returncode == 0
        plan_md = project / TASKS_SUB / task_id / "plan.md"
        plan_md.write_text(plan_md.read_text(encoding="utf-8") + _PLAN_SECTIONS, encoding="utf-8")
        return task_id

    return _make


def _state_of(project: Path, env: dict, task_id: str) -> str:
    """Where the card actually sits — the only proof a shell run really moved it."""
    seen = _rack(project, "context", task_id, env=env)
    assert seen.returncode == 0, seen.stderr
    for line in seen.stdout.splitlines():
        if line.strip().startswith("state:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"`rack context {task_id}` named no state:\n{seen.stdout}")


def _store(env: dict) -> Path:
    """The grant store, resolved the way the gate resolves it — from the envelope."""
    from ai_hats_library.hooks.consent_gate import store_root_from

    identity = json.loads(env["AI_HATS_SESSION_IDENTITY"])
    root = store_root_from(identity.get("session_cache_dir"))
    assert root is not None, "the envelope published no cache dir — J1 is broken"
    return root


def _grants(env: dict) -> list[Path]:
    from ai_hats_library.hooks.consent_gate import grants_dir

    directory = grants_dir(_store(env))
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def _journal(project: Path) -> str:
    path = project / ".git" / "ai-hats" / "bypasses.jsonl"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _issue_verb(project: Path, env: dict, *args: str):
    """Open a window the way a PERSON does — the shipped verb, on PATH, for real."""
    return run_unasked(project, " ".join(("consent", *args)), env=env)


# --- the road as it was, and the road the grant opens --------------------------


def test_without_a_grant_the_first_move_is_still_a_question(project, settings, env, planned):
    """The control: the old road is untouched, ticket and all."""
    task_id = planned("no grant yet")

    verdict = run_chain(project, f"rack transition {task_id} execute", settings=settings, env=env)

    assert verdict.decision == "ask", f"the chain stopped asking without a grant: {verdict}"
    assert verdict.updated_input, "the ask carried no ticket"


def test_the_verb_writes_a_grant_and_prints_the_window_without_the_key(project, env):
    """J3: the file the two readers share is written by the shipped verb, not by us."""
    issued = _issue_verb(project, env, "rack.transition", "30")

    assert issued.returncode == 0, f"the verb failed: {issued.stderr or issued.stdout}"
    assert "rack.transition" in issued.stdout
    written = _grants(env)
    assert len(written) == 1, f"expected one grant, found {written}"
    grant = json.loads(written[0].read_text(encoding="utf-8"))
    assert grant["id"] not in issued.stdout, "the verb printed the key into the model's context"
    assert grant["session_id"] == SESSION_ID
    assert grant["project_dir"] == str(project)


def test_inside_the_window_the_chain_stops_asking_and_the_card_moves(
    project, settings, env, planned
):
    """The value of the whole slice, end to end: one answer, a series of moves.

    Two DIFFERENT cards, because a grant that only covered the invocation it was
    issued for would be the one-shot ticket again. They are walked out of
    ``execute`` between the two gated moves: single-slot ownership (HATS-955)
    lets one session hold one card there, and that rule is not this one's to bend.
    """
    first, second = planned("first move"), planned("second move")
    assert _issue_verb(project, env, "rack.transition", "30").returncode == 0

    for task_id in (first, second):
        verdict = run_chain(
            project, f"rack transition {task_id} execute", settings=settings, env=env
        )
        assert verdict.decision != "ask", f"{task_id} still asked under a live grant: {verdict}"
        assert verdict.updated_input is None, "a grant road must mint no ticket"
        run = run_unasked(project, f"rack transition {task_id} execute", env=env)
        assert run.returncode == 0, run.stderr or run.stdout
        assert _state_of(project, env, task_id) == "execute"
        # Ungated, so it neither needs the grant nor proves anything about it —
        # it only frees the single ownership slot for the next gated move.
        assert _rack(project, "transition", task_id, "document", env=env).returncode == 0


def test_every_use_leaves_a_line_in_the_journal(project, settings, env, planned):
    """ADR-0029 D11: without it, "the grant covered it" and "nobody asked" read alike."""
    task_id = planned("journalled")
    assert _issue_verb(project, env, "rack.transition", "30").returncode == 0
    run_chain(project, f"rack transition {task_id} execute", settings=settings, env=env)
    run_unasked(project, f"rack transition {task_id} execute", env=env)

    journal = _journal(project)
    assert "consent grant" in journal, f"no record of the grant being used:\n{journal}"
    assert "rack.transition" in journal


def _grant_lines(project: Path) -> list[str]:
    return [line for line in _journal(project).splitlines() if "consent grant" in line]


def test_one_move_leaves_exactly_one_record(project, settings, env, planned):
    """One operation, one line — the guard's suppression is a PEEK, not a use.

    Fail-under-revert for S4 (HATS-1736): with the guard journalling its own
    check too, a single gated move wrote TWO records that read alike, so the
    journal over-counted every grant road it gated and "how often was the grant
    used" stopped being answerable from the file.
    """
    task_id = planned("counted once")
    assert _issue_verb(project, env, "rack.transition", "30").returncode == 0
    before = len(_grant_lines(project))

    verdict = run_chain(project, f"rack transition {task_id} execute", settings=settings, env=env)
    assert verdict.decision != "ask", f"the grant did not suppress the question: {verdict}"
    moved = run_unasked(project, f"rack transition {task_id} execute", env=env)
    assert moved.returncode == 0, moved.stderr or moved.stdout

    added = _grant_lines(project)[before:]
    assert len(added) == 1, f"one move wrote {len(added)} records:\n" + "\n".join(added)


def test_the_record_says_what_the_grant_actually_allowed(project, settings, env, planned):
    """A record without the radius cannot tell a narrow grant from `consent all`."""
    task_id = planned("described")
    assert _issue_verb(project, env, "rack.transition", "30").returncode == 0
    before = len(_grant_lines(project))

    run_chain(project, f"rack transition {task_id} execute", settings=settings, env=env)
    run_unasked(project, f"rack transition {task_id} execute", env=env)

    line = _grant_lines(project)[before:][0]
    assert "radius=[rack.transition]" in line, line
    assert "outcome=granted" in line, line
    assert re.search(r"window=\d+m/30m", line), line


# --- the three axes, observed through the whole chain --------------------------


def test_a_grant_of_another_session_does_not_open_this_one(project, settings, env, planned):
    """Restart IS the revocation (ADR-0029 D9), seen from outside."""
    from _helpers.sessions import stand_in_session

    task_id = planned("other session")
    assert _issue_verb(project, env, "rack.transition", "30").returncode == 0
    other = stand_in_session(dict(env), project, "e2e-consent-grant-OTHER")

    verdict = run_chain(project, f"rack transition {task_id} execute", settings=settings, env=other)

    assert verdict.decision == "ask", f"another session's grant opened this one: {verdict}"


def test_an_expired_window_asks_again(project, settings, env, planned):
    """Written by the shipped issuer with its clock moved back — not by hand."""
    from ai_hats_library.hooks.consent_gate import Radius, issue

    task_id = planned("expired")
    issue(
        Radius(types=("rack.transition",)),
        store_root=_store(env),
        session_id=SESSION_ID,
        project_dir=project,
        minutes=1,
        now=time.time() - 3600,
    )

    verdict = run_chain(project, f"rack transition {task_id} execute", settings=settings, env=env)

    assert verdict.decision == "ask", f"an expired window still opened the move: {verdict}"


def test_a_grant_does_not_follow_the_command_into_another_repository(
    project, settings, env, planned, tmp_path
):
    """The guard must not suppress a question for a target it cannot place inside
    the session's project — the property the per-repo ticket store gave for free."""
    task_id = planned("elsewhere")
    elsewhere = tmp_path / "other-repo"
    elsewhere.mkdir()
    assert _issue_verb(project, env, "rack.transition", "30").returncode == 0

    verdict = run_chain(
        project,
        f"cd {elsewhere} && rack transition {task_id} execute",
        settings=settings,
        env=env,
    )

    # `gated`, not `ask`: with no store in the foreign tree the guard cannot raise
    # a question at all and refuses instead. Either way the move does not pass —
    # what must never happen is the grant waving it through (HATS-1735).
    assert verdict.gated, f"the grant reached another repository: {verdict}"
    assert "consent grant" not in _journal(project), "a foreign target was recorded as covered"
