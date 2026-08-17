"""e2e (HATS-1019)

flow:   a developer merging a worktree branch without authorization acknowledgment
cmds:
    ai-hats wt merge task/test-consent
expect: merge is refused without AI_HATS_MERGE_ACK and succeeds when ack is provided
why:    worktree merge is default-deny to ensure supervisor review before landing work"""

from __future__ import annotations
from _helpers.git import git as _git

import subprocess
from pathlib import Path

import pytest


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_wt_merge_consent_gate(shared_launcher, tmp_path):
    """Deny without ack (directive message, branch preserved) → ack merges.

    Scenario:
      1. Bootstrap: git repo + ``self init``.
      2. ``wt create task/test-consent`` + a commit on the task branch.
      3. ``wt merge`` WITHOUT ``AI_HATS_MERGE_ACK`` — exit 1, message names
         the env var and the review handoff, branch + worktree preserved.
      4. ``wt merge`` with ``AI_HATS_MERGE_ACK=1`` — exit 0, work lands.
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    ack_env = dict(env)  # shared_launcher env grants ack by default
    deny_env = dict(env)
    deny_env.pop("AI_HATS_MERGE_ACK", None)

    def ai_hats(*args, expect_exit=0, timeout=180, run_env=ack_env):
        return _run(
            [str(launcher_dest), *args],
            cwd=project,
            env=run_env,
            timeout=timeout,
            expect_exit=expect_exit,
        )

    # ---- 1. bootstrap ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")

    ai_hats("self", "init", "-r", "assistant", "-p", "claude", "--task-prefix", "TST")

    # ---- 2. worktree + task-branch commit ----
    ai_hats("wt", "create", "task/test-consent")
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path = None
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current_path is not None:
            if line[len("branch ") :].strip().endswith("/task/test-consent"):
                wt_path = current_path
                break
    assert wt_path is not None and wt_path.is_dir(), f"could not locate worktree path:\n{listing}"

    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    _git(
        wt_path,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "wt-work",
    )

    # ---- 3. merge without ack: directive refusal ----
    res = ai_hats(
        "wt",
        "merge",
        "task/test-consent",
        expect_exit=1,
        run_env=deny_env,
    )
    combined = res.stdout + res.stderr
    assert "AI_HATS_MERGE_ACK" in combined, f"consent env var not named in refusal:\n{combined}"
    assert "review" in combined.lower(), (
        f"review handoff directive missing from refusal:\n{combined}"
    )
    # HATS-1654: the recipe is followed one line at a time, so the export must
    # reach the merge on the line it was typed with.
    recipe = [ln for ln in combined.splitlines() if "export AI_HATS_MERGE_ACK=1" in ln]
    assert recipe, f"refusal carries no export recipe:\n{combined}"
    assert all("&& ai-hats wt merge" in ln for ln in recipe), (
        f"a lone export dies with the shell that ran it (HATS-1654): {recipe}"
    )

    branches = _git(project, "branch", "--list", "task/test-consent").stdout
    assert "task/test-consent" in branches, (
        f"consent refusal must preserve the task branch:\n{branches}"
    )
    assert wt_path.is_dir(), "consent refusal must preserve the worktree dir"
    assert not (project / "wt-work.txt").exists(), "base must be untouched"

    # ---- 4. merge with ack lands the work ----
    merge_res = ai_hats("wt", "merge", "task/test-consent", run_env=ack_env)

    branches = _git(project, "branch", "--list", "task/test-consent").stdout
    assert branches.strip() == "", (
        f"task branch should be deleted after merge:\n{branches!r}\n"
        f"--- merge stdout ---\n{merge_res.stdout}\n"
        f"--- merge stderr ---\n{merge_res.stderr}"
    )
    log = _git(project, "log", "--all", "--pretty=%s", "-n", "10").stdout
    assert "wt-work" in log, f"worktree commit not in base history:\n{log}"


def _worktree_path(project: Path, branch: str) -> Path:
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    current: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current is not None:
            if line[len("branch ") :].strip().endswith(f"/{branch}"):
                return current
    raise AssertionError(f"could not locate worktree for {branch}:\n{listing}")


@pytest.mark.integration
def test_e2e_merge_refusal_names_every_blocker(shared_launcher, tmp_path):
    """One run names every blocker it can see (HATS-1654).

    Consent refuses first and the base has moved too. If the refusal names only
    the consent, the drift costs a second run — the measured cost was a gate
    rerun in between.
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()
    deny_env = dict(env)
    deny_env.pop("AI_HATS_MERGE_ACK", None)

    def ai_hats(*args, expect_exit=0, timeout=180, run_env=env):
        return _run(
            [str(launcher_dest), *args],
            cwd=project,
            env=run_env,
            timeout=timeout,
            expect_exit=expect_exit,
        )

    def commit(cwd: Path, message: str):
        return _git(
            cwd,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        )

    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    commit(project, "init")

    ai_hats("self", "init", "-r", "assistant", "-p", "claude", "--task-prefix", "TST")
    ai_hats("wt", "create", "task/two-blockers")
    wt_path = _worktree_path(project, "task/two-blockers")
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    commit(wt_path, "wt-work")

    # The base moves under the worktree — the blocker behind the consent one.
    (project / "peer.txt").write_text("peer\n")
    _git(project, "add", "peer.txt")
    commit(project, "peer work")

    res = ai_hats("wt", "merge", "task/two-blockers", expect_exit=1, run_env=deny_env)
    combined = res.stdout + res.stderr
    assert "AI_HATS_MERGE_ACK" in combined, f"consent blocker missing:\n{combined}"
    assert "Also blocking (drift)" in combined, (
        f"the refusal hid the base drift until the next run:\n{combined}"
    )
    assert "wt:pre-merge" in combined, f"what was NOT probed must be said:\n{combined}"


@pytest.mark.integration
def test_e2e_transition_done_is_refused_without_consent(shared_launcher, tmp_path):
    """The canonical supervised close, end to end.

    Was ``…_inner_merge_denied`` until HATS-1682: the refusal used to come from
    the merge INSIDE the teardown and to name ``AI_HATS_MERGE_ACK``. The role
    now declares consent on `review → done` itself, so the edge is refused
    before any worktree is touched — earlier, and by a gate that names the
    point-agnostic channel. What the test protects is unchanged: the refusal is
    directive, the card stays in `review`, the branch survives, and the
    supervisor's answer is what closes it.
    """
    launcher_dest, env, venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    ack_env = dict(env)
    deny_env = dict(env)
    deny_env.pop("AI_HATS_MERGE_ACK", None)
    deny_env["AI_HATS_PLAN_ACK"] = "1"  # plan consent is a different gate
    # The supervisor's answer, on the only channel a hookless subprocess has.
    consented_env = dict(deny_env)
    consented_env["AI_HATS_CONSENT_ACK"] = "1"

    def ai_hats(*args, expect_exit=0, timeout=180, run_env=deny_env):
        return _run(
            [str(launcher_dest), *args],
            cwd=project,
            env=run_env,
            timeout=timeout,
            expect_exit=expect_exit,
        )

    def rack(*args, expect_exit=0, timeout=180, run_env=deny_env):
        return _run(
            [str(venv / "bin" / "rack"), *args],
            cwd=project,
            env=run_env,
            timeout=timeout,
            expect_exit=expect_exit,
        )

    # ---- 1. bootstrap + task → execute ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")
    ai_hats("self", "init", "-r", "assistant", "-p", "claude", "--task-prefix", "TST")

    new_res = rack(
        "create",
        "consent gate test",
        "--description",
        "exercise the HATS-1019 supervised close",
        "--role",
        "assistant",
        "--reviewer",
        "user",
    )
    task_id = None
    for line in new_res.stdout.splitlines():
        if line.strip().startswith("Created:"):
            task_id = line.split()[1]
            break
    assert task_id and task_id.startswith("TST-"), (
        f"could not parse task ID from:\n{new_res.stdout}"
    )

    rack("transition", task_id, "plan")
    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id / "plan.md"
    )
    plan_path.write_text(
        "# Plan\n\n## Requirements\nexercise the consent gate.\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [ ] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )
    rack("transition", task_id, "execute")

    listing = _git(project, "worktree", "list", "--porcelain").stdout
    wt_path: Path | None = None
    current_path: Path | None = None
    task_branch = f"task/{task_id.lower()}"
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current_path is not None:
            if line[len("branch ") :].strip().endswith(f"/{task_branch}"):
                wt_path = current_path
                break
    assert wt_path is not None and wt_path.is_dir(), (
        f"could not locate worktree path for {task_id}:\n{listing}"
    )

    # ---- 2. commit work, walk to review ----
    _git(wt_path, "config", "user.email", "e2e@test")
    _git(wt_path, "config", "user.name", "E2E")
    (wt_path / "wt-work.txt").write_text("wt change\n")
    _git(wt_path, "add", "wt-work.txt")
    _git(
        wt_path,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "wt-work",
    )
    rack("transition", task_id, "document")
    rack("transition", task_id, "review")

    # ---- 3. agent's `done` with no answer: directive refusal, review intact ----
    res = rack("transition", task_id, "done", expect_exit=1)
    combined = res.stdout + res.stderr
    assert "requires supervisor approval" in combined, (
        f"the edge into master was not gated by consent:\n{combined}"
    )
    assert "AI_HATS_CONSENT_ACK" in combined, (
        f"the refusal names no channel the reader can use:\n{combined}"
    )
    assert "review -> done" in combined, f"the refused edge is not named:\n{combined}"
    assert "STOP" in combined, f"review handoff directive missing:\n{combined}"
    branches = _git(project, "branch", "--list", task_branch).stdout
    assert task_branch in branches, "refusal must preserve the task branch"
    assert wt_path.is_dir(), "a refusal before the teardown must preserve the worktree"
    assert not (project / "wt-work.txt").exists(), "base must be untouched by a refusal"
    show = rack("context", task_id).stdout
    assert "state: review" in show, f"card must stay in review after refusal:\n{show}"

    # ---- 4. supervisor merges with the merge ack; the answered close lands ----
    # The close carries `AI_HATS_CONSENT_ACK` and NOT `AI_HATS_MERGE_ACK`: the
    # rack gate reads the point-agnostic channel, and the teardown behind it
    # takes the HATS-596 already-merged short-circuit, which is ack-free.
    ai_hats("wt", "merge", task_branch, run_env=ack_env)
    assert "AI_HATS_MERGE_ACK" not in consented_env
    rack("transition", task_id, "done", run_env=consented_env)

    log = _git(project, "log", "--pretty=%s", "-n", "10").stdout
    assert "wt-work" in log, f"worktree commit not in base history:\n{log}"
    branches = _git(project, "branch", "--list", task_branch).stdout
    assert branches.strip() == "", "task branch should be gone after close"
    closed = rack("context", task_id).stdout
    assert "no question was asked" in closed, (
        f"the env channel closed the card without saying so (HATS-1682 B2):\n{closed}"
    )
