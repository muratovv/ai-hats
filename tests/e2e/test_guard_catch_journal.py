"""e2e (HATS-1634)

flow:   an agent tripping the composed PreToolUse chain — a denied edit, an
        advisory nudge, and a clean command
cmds:
    Write into the main checkout / cat a file / echo hi
expect: every gate that fires appends one catch row to the session's own
        catches.jsonl beside audit.md; a clean command appends nothing
why:    the reviewer may only answer `confirmed` when a guard visibly fired, so a
        firing that leaves no record makes every guard hypothesis unclosable
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _helpers.git import git as _git
from _helpers.hook_chain import build_session_settings, run_chain, run_tool_chain

SESSION_ID = "sid-catch"


def _read(journal: Path) -> list[dict]:
    if not journal.is_file():
        return []
    return [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line]


def _session_dir(project: Path, session_id: str = SESSION_ID) -> Path:
    return project / ".agent/ai-hats/sessions/runs" / f"session_{session_id}"


def _rows(project: Path, session_id: str = SESSION_ID) -> list[dict]:
    return _read(_session_dir(project, session_id) / "catches.jsonl")


def _bypass_rows(project: Path) -> list[dict]:
    """Both homes (HATS-1634): a bypass that named its session went to the
    session dir, one that could not stayed under .git. A reader that knows one
    of them is blind to half the journal — how this file's leak check went
    vacuous."""
    return _read(_session_dir(project) / "bypasses.jsonl") + _read(
        project / ".git/ai-hats/bypasses.jsonl"
    )


@pytest.fixture(scope="module")
def hooked_project(shared_launcher, tmp_path_factory):
    """A real project + a composed session whose hooks live in the session tree."""
    import subprocess

    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("catch-home"))
    # The env must not pre-attribute the session: the whole point is that the
    # hook's own path carries the id.
    env.pop("AI_HATS_SESSION_ID", None)
    env.pop("AI_HATS_DIR", None)

    project = tmp_path_factory.mktemp("catch-proj")
    _git(project, "init", "-b", "master")
    _git(project, "config", "user.email", "t@t.io")
    _git(project, "config", "user.name", "t")
    (project / "seed.txt").write_text("seed\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")

    result = subprocess.run(  # noqa: S603 - launcher path from the session fixture
        [str(launcher), "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(f"self init failed:\n{result.stdout}\n{result.stderr}")

    settings = build_session_settings(project, session_id=SESSION_ID)
    # A live session creates this at start (measured: dir ctime == session id's
    # timestamp), so the gate finds it on the very first tool call.
    (project / ".agent/ai-hats/sessions/runs" / f"session_{SESSION_ID}").mkdir(
        parents=True, exist_ok=True
    )
    return project, env, settings


@pytest.mark.integration
@pytest.mark.xfail(
    reason="the gate is still in KNOWN_UNCAUGHT — S3/S4 wiring removes this marker",
    strict=True,
)
def test_a_denied_edit_records_a_catch_beside_the_session_audit(hooked_project):
    """Fail-under-revert: drop journal_catch from wt_gate.py and this finds no row."""
    project, env, settings = hooked_project
    before = len(_rows(project))

    target = project / "src/app.py"
    verdict = run_tool_chain(
        project, "Write", {"file_path": str(target)}, settings=settings, env=env
    )

    assert verdict.gated, f"the worktree gate must refuse a main-checkout edit; got {verdict}"
    rows = _rows(project)
    assert len(rows) == before + 1, f"expected exactly one new catch, got {rows[before:]}"
    row = rows[-1]
    assert row["kind"] == "catch"
    assert row["hook"] == "wt_gate.py"
    assert row["verdict"] == "deny"
    assert row["rule"] == "worktree-isolation"
    assert row["session_id"] == SESSION_ID, "the hook's own path must attribute the session"


@pytest.mark.integration
@pytest.mark.xfail(
    reason="the gate is still in KNOWN_UNCAUGHT — S3/S4 wiring removes this marker",
    strict=True,
)
def test_an_advisory_nudge_is_recorded_even_though_the_call_proceeds(hooked_project):
    """The 596-nudge blind spot: an allow-with-nudge must still be countable."""
    project, env, settings = hooked_project
    before = len(_rows(project))

    verdict = run_chain(project, "cat seed.txt", settings=settings, env=env)

    assert not verdict.gated, f"the hygiene guard nudges, it never blocks; got {verdict}"
    rows = _rows(project)
    assert len(rows) == before + 1, f"expected exactly one new catch, got {rows[before:]}"
    row = rows[-1]
    assert row["hook"] == "tool_call_hygiene_guard.sh"
    assert row["verdict"] == "nudge"
    assert row["rule"] == "dev_rule_tool_call_hygiene"
    assert row["session_id"] == SESSION_ID


@pytest.mark.integration
@pytest.mark.xfail(
    reason="the gate is still in KNOWN_UNCAUGHT — S3/S4 wiring removes this marker",
    strict=True,
)
def test_an_escalation_is_attributed_to_the_ai_hats_session_not_the_provider_one(hooked_project):
    """`ask` is a firing too — and its gate holds the provider's session UUID.

    Passing that UUID on outranked the ai-hats id the hook's path carries, filing
    the row unattributed. Caught by review, not by a test — so here is the test.
    """
    project, env, settings = hooked_project
    before = len(_rows(project))

    verdict = run_chain(
        project,
        "git push origin master",
        settings=settings,
        env=env,
        # What Claude Code actually sends. Omitting it is what made this blind.
        payload_extra={"session_id": "d290f1ee-6c54-4b01-90e6-d701748f0851"},
    )

    assert verdict.gated, f"an un-acked push must be gated; got {verdict}"
    rows = _rows(project)
    assert len(rows) == before + 1, f"expected exactly one new catch, got {rows[before:]}"
    row = rows[-1]
    assert row["hook"] == "pre_bash_shared_state_guard.sh"
    assert row["rule"] == "rule_pause_before_shared_state_write"
    assert row["session_id"] == SESSION_ID, (
        "attribution must be the ai-hats session from the hook's path, "
        f"never the provider UUID from the payload; got {row['session_id']!r}"
    )


@pytest.mark.integration
def test_a_clean_command_records_nothing(hooked_project):
    """Negative control: without this, a writer that logged everything would pass."""
    project, env, settings = hooked_project
    before = len(_rows(project))

    verdict = run_chain(project, "echo hi", settings=settings, env=env)

    assert not verdict.gated, f"a benign command must pass; got {verdict}"
    assert len(_rows(project)) == before, "a gate that did not fire must record nothing"


@pytest.mark.integration
def test_the_catch_journal_never_touches_the_bypass_journal(hooked_project):
    """Separate files by design: the pre-push report greps bypasses without a
    kind filter, so a catch landing there would read as a bypass (HATS-1407).

    Reads BOTH bypass homes, and makes its own bypass rather than hoping an
    earlier test left one. Checking only `.git`, over a journal nothing had
    written to, is a leak check that cannot fail.
    """
    project, env, settings = hooked_project

    run_tool_chain(
        project,
        "Write",
        {"file_path": str(project / "src/leakcheck.py")},
        settings=settings,
        env={**env, "AI_HATS_WT_GATE_OFF": "1"},
    )
    run_chain(project, "cat seed.txt", settings=settings, env=env)

    kinds = [row.get("kind") for row in _bypass_rows(project)]
    assert kinds, "no bypass rows in either home — the leak check would prove nothing"
    assert "catch" not in kinds, f"a catch leaked into the bypass journal: {kinds}"


@pytest.mark.integration
def test_a_bypass_is_filed_with_its_session_like_the_catch_is(hooked_project):
    """Everything about one session in one directory — the bypass follows the catch.

    Fail-under-revert: send `journal_bypass` back to the repo-wide journal and
    the session dir holds no bypasses.jsonl at all.
    """
    project, env, settings = hooked_project

    verdict = run_tool_chain(
        project,
        "Write",
        {"file_path": str(project / "src/hatched.py")},
        settings=settings,
        env={**env, "AI_HATS_WT_GATE_OFF": "1"},
    )

    assert not verdict.gated, f"the kill switch must let the edit through; got {verdict}"
    rows = _read(_session_dir(project) / "bypasses.jsonl")
    assert rows, "a hatch left no bypass row beside the session's audit.md"
    row = rows[-1]
    assert row["kind"] == "hatch"
    assert row["reason"] == "AI_HATS_WT_GATE_OFF"
    assert row["session_id"] == SESSION_ID, (
        "the runtime tier gets no AI_HATS_SESSION_ID from the harness, so the "
        f"hook's own path is what must attribute it; got {row['session_id']!r}"
    )
