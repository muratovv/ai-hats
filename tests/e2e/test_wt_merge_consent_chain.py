"""e2e (HATS-1682)

flow:   an agent merging its worktree straight into master, asked about in chat
cmds:   ai-hats wt merge [task/one-click]
expect: the composed chain answers `ask` with a one-shot ticket, and the command
        it approved — run verbatim through a real shell, with no AI_HATS_MERGE_ACK
        anywhere in the environment — lands the merge, once
why:    the click here was inert: the guard minted under the branch it saw while
        the CLI looked for a card, so the merge still demanded the env ack
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.git import branches, commit_file, git as _git, log_subjects, worktrees
from _helpers.hook_chain import Verdict, build_session_settings, run_approved, run_chain

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
#: Minted by `safety_gate.py` when the supervisor answers, spent by `ai-hats wt
#: merge` once the merge has happened. An agent typing one is refused.
TICKET_ENV = "AI_HATS_CONSENT_TICKET"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A checkout on `master` that `ai-hats wt` can drive."""
    from ai_hats.assembler import Assembler
    from ai_hats.models import ProjectConfig

    from _helpers.git import init_repo

    root = tmp_path / "proj"
    root.mkdir()
    init_repo(root, branch="master")
    ProjectConfig(provider="claude", library_paths=[]).save(root / "ai-hats.yaml")
    Assembler(root).init()
    return root


@pytest.fixture
def env(project: Path, tmp_path: Path, ai_hats_shim: Path) -> dict:
    """A session env with NO consent anywhere — the point of the whole file.

    ``tests/conftest.py`` grants both acks to every test and they ride into a
    subprocess through inheritance, so a test that leaves them in place proves
    nothing about consent (HATS-1682 T4).
    """
    from _helpers.env import checkout_pythonpath
    from _helpers.sessions import stand_in_session

    e = os.environ.copy()
    for name in [k for k in e if k.startswith("AI_HATS_") and k.endswith("ACK")]:
        del e[name]
    e.pop(TICKET_ENV, None)
    e["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, e.get("PYTHONPATH", ""))
    e["AI_HATS_USER_HOME"] = str(tmp_path / "user-home")
    e["AI_HATS_ROOT_PID"] = str(os.getpid())
    # The approved command line says `ai-hats`, so a real one has to be on PATH:
    # the console script is no longer built (HATS-790), the shim is it.
    e["PATH"] = os.pathsep.join([str(ai_hats_shim.parent), e.get("PATH", "")])
    return stand_in_session(e, project, "e2e-wt-merge-consent")


@pytest.fixture
def settings(project: Path) -> Path:
    return build_session_settings(project)


@pytest.fixture
def ai_hats(project: Path, env: dict, ai_hats_shim: Path):
    """Run `ai-hats …` in the project, off the chain — setup, not the subject."""

    def _run(*args: str, expect: int | None = 0) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(  # noqa: S603 - our own shim, literal argv
            [str(ai_hats_shim), *args],
            cwd=str(project),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if expect is not None and proc.returncode != expect:
            raise AssertionError(
                f"`ai-hats {' '.join(args)}` exited {proc.returncode}, wanted {expect}\n"
                f"{proc.stdout}\n{proc.stderr}"
            )
        return proc

    return _run


@pytest.fixture
def branch_with_work(project: Path, ai_hats):
    """A worktree branch carrying one commit — something a merge can land."""

    def _make(branch: str, note: str = "wt-work") -> Path:
        ai_hats("wt", "create", branch)
        wt_path = worktrees(project)[branch]
        (wt_path / f"{note}.txt").write_text(f"{note}\n")
        _git(wt_path, "add", "-A")
        _git(wt_path, "commit", "-m", note)
        return wt_path

    return _make


def _assert_no_consent_anywhere(env: dict) -> None:
    granted = sorted(k for k in env if k.startswith("AI_HATS_") and "ACK" in k)
    assert not granted, f"the environment already consents: {granted}"


def _ask_to_merge(project: Path, settings: Path, env: dict, command: str) -> Verdict:
    """The chain's `ask` for a direct merge, ticket and all."""
    verdict = run_chain(project, command, settings=settings, env=env)
    assert verdict.decision == "ask", f"the road into master was not gated: {verdict}"
    assert verdict.hook == "safety_gate.py", f"another hook answered: {verdict}"
    assert verdict.updated_input, f"the ask carried no ticket: {verdict}"
    assert verdict.updated_input["command"].startswith(f"{TICKET_ENV}="), verdict.updated_input
    return verdict


@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param("ai-hats wt merge task/one-click", id="branch-typed"),
        # Left off, the guard's label is the literal "this worktree" while the
        # CLI resolves a real branch name — the case that cannot be matched by
        # recomputing the label, only by dropping the axis (HATS-1682 A1).
        pytest.param("ai-hats wt merge", id="branch-omitted"),
    ],
)
def test_one_click_lands_the_merge_with_no_ack_in_the_environment(
    project, settings, env, branch_with_work, spelling
):
    """The whole road, end to end: question → answer → the branch is in master.

    Before this, the answer matched nothing: the CLI peeked for a card the guard
    never wrote, so `merge(consent=False)` refused and told the supervisor to
    export `AI_HATS_MERGE_ACK`. The click was burnt and the question bought
    nothing at all.
    """
    branch_with_work("task/one-click")
    _assert_no_consent_anywhere(env)

    verdict = _ask_to_merge(project, settings, env, spelling)
    merged = run_approved(project, verdict, env=env)

    assert merged.returncode == 0, f"the approved merge was refused:\n{merged}"
    assert "AI_HATS_MERGE_ACK" not in merged.output, (
        f"one answered question still asked for the env ack:\n{merged}"
    )
    assert branches(project) == ["master"], f"the task branch outlived the merge:\n{merged}"
    assert "wt-work" in log_subjects(project), f"the work never reached master:\n{merged}"


def test_the_spent_ticket_does_not_open_a_second_merge(project, settings, env, branch_with_work):
    """One answer, one merge. The wt road only ever peeked, so the same approved
    line re-run inside the store's 12h housekeeping window merged again, unasked
    (HATS-1682 B8)."""
    branch_with_work("task/replay")
    verdict = _ask_to_merge(project, settings, env, "ai-hats wt merge task/replay")
    assert run_approved(project, verdict, env=env).returncode == 0

    # Same branch name, same command line, same session: the ticket's single use
    # is the only thing standing between it and a second unasked merge.
    branch_with_work("task/replay", note="wt-work-again")
    replayed = run_approved(project, verdict, env=env)

    assert replayed.returncode != 0, f"a spent ticket merged again:\n{replayed}"
    assert "AI_HATS_MERGE_ACK" in replayed.output, f"the refusal named no way out:\n{replayed}"
    assert "wt-work-again" not in log_subjects(project), "the second merge landed anyway"


def test_a_ticket_in_hand_is_not_reported_as_a_blocker(project, settings, env, branch_with_work):
    """A merge held up by drift must not also say consent is missing.

    The refusal lists every OTHER blocker so one run costs one round trip
    (HATS-1654) — and it probed consent without being told the supervisor had
    already given it, so a ticket-bearing merge was told to export the env ack:
    exactly the complaint that card was filed for (HATS-1682 B7).
    """
    branch_with_work("task/two-blockers")
    commit_file(project, "peer.txt", "peer\n", "peer work")  # the base moves

    verdict = _ask_to_merge(project, settings, env, "ai-hats wt merge task/two-blockers")
    refused = run_approved(project, verdict, env=env)

    assert refused.returncode == 1, f"the drifted merge was not refused:\n{refused}"
    assert "Refused (drift)" in refused.output, f"drift is not what refused:\n{refused}"
    assert "Also blocking (consent)" not in refused.output, (
        f"the supervisor's own ticket was reported as a blocker:\n{refused}"
    )
    assert "AI_HATS_MERGE_ACK" not in refused.output, (
        f"a merge holding consent was sent for the env ack:\n{refused}"
    )
