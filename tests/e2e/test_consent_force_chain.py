"""e2e (HATS-1682)

flow:   an agent taking the documented force-close, and the headless road home
cmds:
    rack transition SBX-001 --state done --force --reason close
    AI_HATS_CONSENT_ACK=1 rack transition SBX-001 done
expect: the composed PreToolUse chain ASKS on the forced close, the engine
        refuses it until the answer arrives, and one env flag — with no
        AI_HATS_MERGE_ACK anywhere — carries `review → done` through the merge
why:    `--force` applies to the OPERATION. Consent is not a property of the
        command, so nothing ADDED to a command can switch it off:
        `consent | op --force`. In the incident that opened this card, that
        exact spelling merged a branch into master with no question at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.git import git as _git
from _helpers.hook_chain import build_session_settings, run_approved, run_chain

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
TICKET_ENV = "AI_HATS_CONSENT_TICKET"
CONSENT_ACK = "AI_HATS_CONSENT_ACK"
MERGE_ACK = "AI_HATS_MERGE_ACK"

_PLAN_SECTIONS = (
    "\n## Requirements\nx\n## Approach & counter\nx\n"
    "## Scope & Out-of-scope\nx\n## Steps\n1. x\n## Verification Protocol\nx\n"
)


def _rack(project: Path, *args: str, env: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own module, literal argv
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
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
def env(project: Path) -> dict:
    """A session env with EVERY consent flag stripped — including the merge ack.

    The root conftest grants ``AI_HATS_MERGE_ACK`` to every test, and that is
    the exact environment the live probe named as the incident condition
    (HATS-1682 T4). A file about consent cannot inherit it.
    """
    from _helpers.env import checkout_pythonpath
    from _helpers.sessions import stand_in_session

    e = os.environ.copy()
    for flag in ("AI_HATS_PLAN_ACK", CONSENT_ACK, MERGE_ACK, TICKET_ENV):
        e.pop(flag, None)
    e["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, e.get("PYTHONPATH", ""))
    e["AI_HATS_ROOT_PID"] = str(os.getpid())
    return stand_in_session(e, project, "e2e-consent-force")


@pytest.fixture
def settings(project: Path) -> Path:
    return build_session_settings(project)


@pytest.fixture
def planned(project: Path, env: dict):
    """A card sitting in ``plan`` with a filled plan.md."""

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
    seen = _rack(project, "context", task_id, env=env)
    assert seen.returncode == 0, seen.stderr
    for line in seen.stdout.splitlines():
        if line.strip().startswith("state:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"`rack context {task_id}` named no state:\n{seen.stdout}")


def _worktree_of(project: Path, task_id: str) -> Path:
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    current: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current is not None:
            if line[len("branch ") :].strip().endswith(f"/task/{task_id.lower()}"):
                return current
    raise AssertionError(f"no worktree for {task_id}:\n{listing}")


@pytest.fixture
def reviewed(project: Path, env: dict, planned):
    """A card in ``review`` whose branch carries a commit of its own.

    Without that commit the branch is already an ancestor of master and the
    merge takes the HATS-596 already-merged short-circuit, which is
    consent-free by design — the gate under test would never be reached.
    """

    def _make() -> str:
        task_id = planned("into master")
        walk = {**env, CONSENT_ACK: "1"}  # only the LAST edge is under test
        for state in ("execute", "document", "review"):
            moved = _rack(project, "transition", task_id, state, env=walk)
            assert moved.returncode == 0, moved.stdout + moved.stderr
        worktree = _worktree_of(project, task_id)
        (worktree / "work.txt").write_text("work\n")
        _git(worktree, "add", "work.txt")
        _git(worktree, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
             "commit", "-m", "work")  # fmt: skip
        return task_id

    return _make


def _hatches(project: Path) -> list[dict]:
    journal = project / ".git" / "ai-hats" / "bypasses.jsonl"
    if not journal.is_file():
        return []
    records = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
    return [r for r in records if r.get("kind") == "hatch"]


@pytest.mark.parametrize(
    "addition",
    [
        pytest.param("", id="bare"),
        # The documented `close` recipe (trait-agent, hatrack/SKILL.md,
        # ARCHITECTURE.md) — and the shape the incident was measured on.
        pytest.param(" --force --reason close", id="force"),
        pytest.param(" --json", id="json"),
        pytest.param(" --ack-frozen", id="ack-frozen"),
    ],
)
def test_no_addition_to_the_command_removes_the_question(project, settings, env, planned, addition):
    """The principle, not the one flag.

    `RACK_UNGATED_FLAGS` used to hold `--force` and the whole SET is gone, not
    that one entry: a list of flags we do not ask on contradicts
    `consent | op --force` as a category, and the next flag added would join it
    in silence. The `bare` case is the control — it says the other three are
    being compared against a chain that does ask.
    """
    task_id = planned("addition")

    verdict = run_chain(
        project, f"rack transition {task_id} --state done{addition}", settings=settings, env=env
    )

    assert verdict.decision == "ask", f"`--state done{addition}` was not gated: {verdict}"
    assert verdict.hook == "safety_gate.py", f"another hook answered: {verdict}"
    assert verdict.updated_input["command"].startswith(f"{TICKET_ENV}="), verdict.updated_input


def test_the_documented_force_close_asks_once_and_then_works(project, settings, env, reviewed):
    """The incident, end to end: refused unanswered, then carried by the click.

    Measured before the fix: in a session exporting `AI_HATS_MERGE_ACK`,
    `rack transition <ID> --state done --force` merged into master with no
    question and no refusal. Three layers were open at once; this walks all
    three — the guard asks, the engine refuses, the ticket opens it.
    """
    task_id = reviewed()
    forced = ("transition", task_id, "--state", "done", "--force", "--reason", "close")

    refused = _rack(project, *forced, env=env)

    assert refused.returncode != 0, f"the forced close was waved through:\n{refused.stdout}"
    assert "requires supervisor approval" in refused.stdout + refused.stderr
    assert _state_of(project, env, task_id) == "review"

    command = f"rack transition {task_id} --state done --force --reason close"
    verdict = run_chain(project, command, settings=settings, env=env)
    assert verdict.decision == "ask", f"the guard stayed silent on the forced close: {verdict}"

    moved = run_approved(project, verdict, env=env)

    assert moved.returncode == 0, f"the answered forced close was still refused:\n{moved}"
    assert _state_of(project, env, task_id) == "done"


def test_consent_ack_alone_carries_review_to_done(project, env, reviewed):
    """Requirement 8's headless road, across a process boundary and ONE flag.

    `AI_HATS_CONSENT_ACK` passed the rack gate and the merge inside the
    teardown then refused anyway for want of `AI_HATS_MERGE_ACK`, so the
    documented single-flag road needed two and no text said so (B1).
    """
    task_id = reviewed()
    headless = {**env, CONSENT_ACK: "1"}
    assert MERGE_ACK not in headless

    closed = _rack(project, "transition", task_id, "done", env=headless)

    assert closed.returncode == 0, f"one flag was not enough:\n{closed.stdout}{closed.stderr}"
    assert _state_of(project, env, task_id) == "done"
    merged = _git(project, "log", "--pretty=%s", "-n", "20").stdout
    assert "work" in merged, f"the branch never reached master:\n{merged}"


def test_the_headless_road_is_loud_on_both_gates(project, env, reviewed):
    """B2: the env channel is allowed where nothing can ask, never quiet.

    Two writers, because two gates hatched — the rack's edge gate and the merge
    inside the teardown. On a hookless surface the journal and the card are the
    only trace a consented `→ done` ever leaves.
    """
    task_id = reviewed()
    # The setup walk hatched on the env channel too, so only what THIS close
    # wrote counts — a set read whole would pass on the walk's entries alone.
    before = len(_hatches(project))

    closed = _rack(project, "transition", task_id, "done", env={**env, CONSENT_ACK: "1"})
    assert closed.returncode == 0, closed.stdout + closed.stderr

    hatched = {r["hook"] for r in _hatches(project)[before:] if r["reason"] == CONSENT_ACK}
    assert hatched == {"rack_wiring.py", "wt_effects.py"}, (
        f"a gate hatched on the env channel without a journal entry: {hatched}"
    )
    seen = _rack(project, "context", task_id, env=env).stdout
    assert "no question was asked" in seen, f"the card does not say how it closed:\n{seen}"
