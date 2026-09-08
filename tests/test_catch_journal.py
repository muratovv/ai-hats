"""HATS-1634 — the catch journal: a gate that FIRED must leave a record.

Attribution is the file's LOCATION, not a field: a catch lands beside ``audit.md``
in the session's artefact dir, and the session id comes from the hook's own
materialized path — runtime hooks provably do not receive ``AI_HATS_SESSION_ID``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/hooks"
PY = HOOKS / "bypass_journal.py"
SH = HOOKS / "bypass_journal.sh"

SESSION_ID = "20260813-191049-1-27020"


@pytest.fixture
def writer():
    """The canon writer, imported by location like the hooks themselves do."""
    spec = importlib.util.spec_from_file_location("bypass_journal", PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bypass_journal"] = module
    spec.loader.exec_module(module)
    return module


def _git_project(root: Path) -> Path:
    """A real git repo — the writer resolves its target through git."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _session_dir(project: Path, session_id: str = SESSION_ID) -> Path:
    """The auditor's dir, created at session start — audit.md's neighbour."""
    sdir = project / ".agent/ai-hats/sessions/runs" / f"session_{session_id}"
    sdir.mkdir(parents=True)
    (sdir / "audit.md").write_text("# Session Audit\n", encoding="utf-8")
    return sdir


def _materialized_hook(cache: Path, session_id: str = SESSION_ID) -> Path:
    """A hook where the harness actually invokes it — inside the session tree."""
    hook = cache / "sessions" / session_id / "plugin/skills/worktree-isolation/hooks/wt_gate.py"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return hook


def test_catch_lands_in_the_session_dir_its_own_path_names(writer, tmp_path):
    project = _git_project(tmp_path / "proj")
    sdir = _session_dir(project)
    hook = _materialized_hook(tmp_path / "cache")

    ok = writer.journal_catch(
        "worktree-isolation",
        "deny",
        hook="wt_gate.py",
        hook_path=str(hook),
        cmd="Edit /main/checkout/src/x.py",
        cwd=project,
    )

    assert ok is True
    journal = sdir / "catches.jsonl"
    assert journal.is_file(), f"no catch journal beside audit.md; dir holds {list(sdir.iterdir())}"
    entry = json.loads(journal.read_text(encoding="utf-8").strip())
    assert entry["kind"] == "catch"
    assert entry["rule"] == "worktree-isolation"
    assert entry["verdict"] == "deny"
    assert entry["hook"] == "wt_gate.py"
    assert entry["session_id"] == SESSION_ID


def test_a_bypass_that_knows_its_session_lands_in_the_session_dir(writer, tmp_path):
    """Everything about one session in one directory — the bypass follows the catch.

    The runtime tier never filled `session_id` from the environment (5 rows in
    3898); reading it off the hook's own path is what makes the routing possible
    at all, so this asserts the field AND the location together.
    """
    project = _git_project(tmp_path / "proj")
    sdir = _session_dir(project)
    hook = _materialized_hook(tmp_path / "cache")

    ok = writer.journal_bypass(
        "hatch", "AI_HATS_WT_GATE_OFF", hook="wt_gate.py", hook_path=str(hook), cwd=project
    )

    assert ok is True
    journal = sdir / "bypasses.jsonl"
    assert journal.is_file(), f"bypass not beside audit.md; dir holds {list(sdir.iterdir())}"
    entry = json.loads(journal.read_text(encoding="utf-8").strip())
    assert entry["session_id"] == SESSION_ID
    assert entry["kind"] == "hatch"
    assert not (project / ".git/ai-hats/bypasses.jsonl").exists(), (
        "a session-attributed bypass must MOVE, not be copied into both journals"
    )


def test_a_bypass_with_no_session_keeps_the_repo_wide_journal(writer, tmp_path, monkeypatch):
    """The default path under .git stays for every row that cannot name a session —
    94% of today's rows, and the only address `pre-push-bypass-report.sh` knows."""
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)
    project = _git_project(tmp_path / "proj")

    ok = writer.journal_bypass(
        "hatch", "AI_HATS_PRIVACY_ACK", hook="pre-commit-privacy.sh", hook_path="", cwd=project
    )

    assert ok is True
    journal = project / ".git/ai-hats/bypasses.jsonl"
    assert journal.is_file(), "a session-less bypass lost its home"
    assert json.loads(journal.read_text(encoding="utf-8").strip())["session_id"] == ""


def test_a_catch_with_no_session_dir_is_recorded_unattributed_and_says_so(writer, tmp_path, capsys):
    """A fallback that cannot report is forbidden — dev_rule_silent_fallback."""
    project = _git_project(tmp_path / "proj")
    hook = _materialized_hook(tmp_path / "cache")  # session tree, but no session dir

    ok = writer.journal_catch(
        "worktree-isolation", "deny", hook="wt_gate.py", hook_path=str(hook), cwd=project
    )

    assert ok is True, "an unattributed catch is still a catch — losing it is the defect"
    fallback = project / ".git/ai-hats/catches.jsonl"
    assert fallback.is_file(), "catch vanished when the session dir was absent"
    entry = json.loads(fallback.read_text(encoding="utf-8").strip())
    assert entry["session_id"] == "", "an unattributed row must not claim a session"
    assert "unattributed" in capsys.readouterr().err


def test_the_git_tier_falls_back_to_the_environment_for_attribution(writer, tmp_path, monkeypatch):
    """Git gates run from .githooks/, so their path carries no session id."""
    project = _git_project(tmp_path / "proj")
    sdir = _session_dir(project)
    monkeypatch.setenv("AI_HATS_SESSION_ID", SESSION_ID)

    ok = writer.journal_catch(
        "privacy",
        "block",
        hook="pre-commit-privacy.sh",
        hook_path="/x/.githooks/pre-commit",
        cwd=project,
    )

    assert ok is True
    entry = json.loads((sdir / "catches.jsonl").read_text(encoding="utf-8").strip())
    assert entry["session_id"] == SESSION_ID


@pytest.mark.integration
def test_both_twins_write_one_identical_catch_shape(writer, tmp_path):
    """The shell twin serves git gates, the python twin the stdlib-only hooks.

    A field added to one and forgotten in the other splits the journal into two
    halves a reader silently mis-parses — the HATS-1407 defect, catch edition.
    """
    project = _git_project(tmp_path / "proj")
    sdir = _session_dir(project)
    shell_hook = (
        tmp_path / "cache/sessions" / SESSION_ID / "plugin/skills/tool-call-hygiene/hooks/g.sh"
    )
    shell_hook.parent.mkdir(parents=True)

    script = f'. "{SH}"\nai_hats_journal_catch dev_rule_tool_call_hygiene nudge "cat x.py" ""\n'
    driver = shell_hook.parent / "driver.sh"
    driver.write_text(script, encoding="utf-8")
    subprocess.run(  # noqa: S603 — our own script
        ["bash", str(driver)],  # noqa: S607 — bash from PATH, as a hook runs it
        cwd=project,
        check=True,
        env={**os.environ, "AI_HATS_SESSION_ID": SESSION_ID},
    )
    writer.journal_catch(
        "worktree-isolation", "deny", hook="wt_gate.py", session_id=SESSION_ID, cwd=project
    )

    lines = (sdir / "catches.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, lines
    shell_entry, py_entry = json.loads(lines[0]), json.loads(lines[1])
    assert tuple(shell_entry.keys()) == writer.CATCH_FIELDS
    assert tuple(py_entry.keys()) == writer.CATCH_FIELDS
    assert shell_entry["rule"] == "dev_rule_tool_call_hygiene"
    assert shell_entry["verdict"] == "nudge"
    assert shell_entry["hook"] == "driver.sh"


def test_a_foreign_ai_hats_dir_never_diverts_the_catch(writer, tmp_path, monkeypatch):
    """AI_HATS_DIR leaks between checkouts (done-gate.sh refuses it for this reason).

    Honouring it filed the record under whatever project the variable named —
    another project's telemetry, corrupted silently. Measured, not theorised: the
    first benchmark run went unattributed because the developer's own env had it.
    """
    project = _git_project(tmp_path / "proj")
    sdir = _session_dir(project)
    foreign = _git_project(tmp_path / "foreign")
    (foreign / ".agent/ai-hats/sessions/runs" / f"session_{SESSION_ID}").mkdir(parents=True)
    monkeypatch.setenv("AI_HATS_DIR", str(foreign / ".agent/ai-hats"))

    writer.journal_catch(
        "worktree-isolation", "deny", hook="wt_gate.py", session_id=SESSION_ID, cwd=project
    )

    assert (sdir / "catches.jsonl").is_file(), "the catch must follow the repo, not the env"
    assert not (
        foreign / ".agent/ai-hats/sessions/runs" / f"session_{SESSION_ID}" / "catches.jsonl"
    ).exists()


def test_the_mirrored_session_path_still_matches_the_one_ai_hats_resolves(writer, monkeypatch):
    """The hook cannot import ai_hats, so it mirrors the path — pin the mirror.

    A silent drift here files every catch where no auditor looks.
    """
    from ai_hats_observe.artifacts import SESSION_PREFIX, session_dirname

    from ai_hats.paths import runs_dir

    monkeypatch.delenv("AI_HATS_DIR", raising=False)  # else the env wins over the default
    project = Path("/proj")

    mirrored = (
        project / writer.AI_HATS_REL / writer.RUNS_REL / f"{writer.SESSION_PREFIX}{SESSION_ID}"
    )
    assert mirrored == runs_dir(project) / session_dirname(SESSION_ID)
    assert writer.SESSION_PREFIX == SESSION_PREFIX
