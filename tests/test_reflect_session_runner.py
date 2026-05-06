"""Tests for ReflectSessionRunner — happy path, retry, validation, save."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_hats.retro.reflect_session import (
    REFLECT_DELIM_END,
    REFLECT_DELIM_START,
    ReflectSessionError,
    ReflectSessionRunner,
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".gitlog").mkdir()
    (tmp_path / ".agent" / "hypotheses").mkdir(parents=True)
    (tmp_path / ".agent" / "backlog" / "proposals").mkdir(parents=True)
    return tmp_path


def _make_session(project: Path, session_id: str) -> None:
    sdir = project / ".gitlog" / f"session_{session_id}"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "audit.md").write_text(f"# Session {session_id}\n## Turn 1\n")
    (sdir / "metrics.json").write_text(
        '{"role": "test", "turns": 1, "tool_calls": 2}'
    )


def _make_hyp(project: Path, hyp_id: str = "HYP-001", status: str = "active"):
    body = {
        "id": hyp_id,
        "title": f"hyp-{hyp_id}",
        "status": status,
        "created": "2026-01-01",
        "source_task": "HATS-001",
        "hypothesis": "h",
        "validation_log": [],
    }
    (project / ".agent" / "hypotheses" / f"{hyp_id}.yaml").write_text(
        yaml.safe_dump(body)
    )


class _FakeSession:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir


class FakeSubAgentRunner:
    def __init__(self, project_dir: Path, transcripts: list[str]) -> None:
        self.project_dir = project_dir
        self._transcripts = list(transcripts)
        self.calls: list[dict] = []
        self._counter = 0

    def run(
        self, role_name: str, task: str = "", ticket_id: str = "",
        model: str = "", parent_session=None, isolation_mode: str = "discard",
    ) -> _FakeSession:
        self.calls.append({"role": role_name, "task": task[:100], "model": model})
        self._counter += 1
        sdir = self.project_dir / ".gitlog" / f"session_fake_{self._counter}"
        sdir.mkdir(parents=True, exist_ok=True)
        if self._transcripts:
            (sdir / "transcript.txt").write_text(self._transcripts.pop(0))
        return _FakeSession(sdir)


def _valid_reflect_retro(session_id: str, hyp_ids: list[str]) -> str:
    verdicts = "\n".join(
        f"  - hyp_id: {h}\n"
        f"    verdict: inconclusive\n"
        f"    evidence: not enough data\n"
        f"    recommendation: keep"
        for h in hyp_ids
    )
    return f"""---
schema: hats-reflect-session/v1
session_id: {session_id}
timestamp: '2026-05-04T12:00:00+00:00'
hypothesis_verdicts:
{verdicts if verdicts else '  []'}
proposal_actions: []
self_problems: []
---

# Reflect-session output for {session_id}
"""


def _wrap(content: str) -> str:
    return f"preamble\n{REFLECT_DELIM_START}\n{content}\n{REFLECT_DELIM_END}\n"


# ------------------- Happy path -------------------

def test_run_saves_retro(tmp_path: Path):
    p = _project(tmp_path)
    _make_session(p, "20260504-120000-1")
    _make_hyp(p, "HYP-001")
    transcript = _wrap(_valid_reflect_retro("20260504-120000-1", ["HYP-001"]))
    fake = FakeSubAgentRunner(p, [transcript])
    runner = ReflectSessionRunner(p, subagent_runner=fake)

    path = runner.run("20260504-120000-1")
    assert path.exists()
    assert path.parent == p / ".agent" / "retrospectives" / "reflect-session"
    # No meta-proposal on success
    proposals = list((p / ".agent" / "backlog" / "proposals").glob("PROP-*.yaml"))
    assert proposals == []


def test_run_passes_reflect_model_to_runner(tmp_path: Path):
    """HATS-232: feedback.session_retro.reflect_model is propagated to SubAgentRunner.run(model=...)."""
    p = _project(tmp_path)
    (p / "ai-hats.yaml").write_text(
        "provider: claude\n"
        "schema_version: 2\n"
        "feedback:\n"
        "  session_retro:\n"
        "    reflect_model: claude-haiku-4-5\n"
    )
    _make_session(p, "s-mdl")
    _make_hyp(p, "HYP-001")
    transcript = _wrap(_valid_reflect_retro("s-mdl", ["HYP-001"]))
    fake = FakeSubAgentRunner(p, [transcript])
    runner = ReflectSessionRunner(p, subagent_runner=fake)

    runner.run("s-mdl")
    assert fake.calls, "sub-agent runner was not invoked"
    assert fake.calls[0]["model"] == "claude-haiku-4-5"


def test_run_omits_model_when_reflect_model_unset(tmp_path: Path):
    """No reflect_model in config → runner.run receives empty string (CLI default applies)."""
    p = _project(tmp_path)
    (p / "ai-hats.yaml").write_text(
        "provider: claude\nschema_version: 2\n"
    )
    _make_session(p, "s-no-mdl")
    _make_hyp(p, "HYP-001")
    transcript = _wrap(_valid_reflect_retro("s-no-mdl", ["HYP-001"]))
    fake = FakeSubAgentRunner(p, [transcript])
    runner = ReflectSessionRunner(p, subagent_runner=fake)

    runner.run("s-no-mdl")
    assert fake.calls[0]["model"] == ""


def test_run_includes_active_hypotheses_in_prompt(tmp_path: Path):
    p = _project(tmp_path)
    _make_session(p, "s1")
    _make_hyp(p, "HYP-001")
    _make_hyp(p, "HYP-002", status="confirmed")  # not active
    transcript = _wrap(_valid_reflect_retro("s1", ["HYP-001"]))
    fake = FakeSubAgentRunner(p, [transcript])
    runner = ReflectSessionRunner(p, subagent_runner=fake)
    runner.run("s1")
    assert fake.calls[0]["role"] == "reflect-session"


# ------------------- No-silent-failure tests (CRITICAL) -------------------

def test_subprocess_crash_creates_meta_proposal(tmp_path: Path):
    """Empty transcript (subprocess crash) → programmatic meta-proposal."""
    p = _project(tmp_path)
    _make_session(p, "s1")
    _make_hyp(p, "HYP-001")
    fake = FakeSubAgentRunner(p, ["", ""])  # empty transcripts both attempts
    runner = ReflectSessionRunner(p, subagent_runner=fake)

    with pytest.raises(ReflectSessionError):
        runner.run("s1")

    proposals = list((p / ".agent" / "backlog" / "proposals").glob("PROP-*.yaml"))
    assert len(proposals) == 1
    data = yaml.safe_load(proposals[0].read_text())
    assert data["category"] == "process"
    assert data["target"] == "reflect-session"
    assert data["failed_session_id"] == "s1"


def test_invalid_yaml_creates_meta_proposal(tmp_path: Path):
    """Malformed frontmatter (no closing ---) → meta-proposal."""
    p = _project(tmp_path)
    _make_session(p, "s1")
    _make_hyp(p, "HYP-001")
    bad = _wrap("---\nschema: hats-reflect-session/v1\nNOT FINISHED")
    fake = FakeSubAgentRunner(p, [bad, bad])
    runner = ReflectSessionRunner(p, subagent_runner=fake)

    with pytest.raises(ReflectSessionError):
        runner.run("s1")

    proposals = list((p / ".agent" / "backlog" / "proposals").glob("PROP-*.yaml"))
    assert len(proposals) == 1
    data = yaml.safe_load(proposals[0].read_text())
    assert data["failed_session_id"] == "s1"


def test_missing_hypothesis_verdict_creates_meta_proposal(tmp_path: Path):
    """Output omits a verdict for an active HYP → integrity fail → meta-proposal."""
    p = _project(tmp_path)
    _make_session(p, "s1")
    _make_hyp(p, "HYP-001")
    _make_hyp(p, "HYP-002")
    # Output votes only HYP-001, missing HYP-002
    incomplete = _wrap(_valid_reflect_retro("s1", ["HYP-001"]))
    fake = FakeSubAgentRunner(p, [incomplete, incomplete])
    runner = ReflectSessionRunner(p, subagent_runner=fake)

    with pytest.raises(ReflectSessionError):
        runner.run("s1")

    proposals = list((p / ".agent" / "backlog" / "proposals").glob("PROP-*.yaml"))
    assert len(proposals) == 1
    desc = yaml.safe_load(proposals[0].read_text())["description"]
    assert "HYP-002" in desc or "missing" in desc.lower()


def test_session_id_mismatch_creates_meta_proposal(tmp_path: Path):
    """Output references wrong session_id → integrity fail → meta-proposal."""
    p = _project(tmp_path)
    _make_session(p, "s-actual")
    _make_hyp(p, "HYP-001")
    wrong = _wrap(_valid_reflect_retro("s-other", ["HYP-001"]))
    fake = FakeSubAgentRunner(p, [wrong, wrong])
    runner = ReflectSessionRunner(p, subagent_runner=fake)

    with pytest.raises(ReflectSessionError):
        runner.run("s-actual")

    proposals = list((p / ".agent" / "backlog" / "proposals").glob("PROP-*.yaml"))
    assert len(proposals) == 1


def test_schema_validation_failure_creates_meta_proposal(tmp_path: Path):
    """Pydantic validation error (e.g. bad enum) → meta-proposal."""
    p = _project(tmp_path)
    _make_session(p, "s1")
    _make_hyp(p, "HYP-001")
    bad = _wrap("""---
schema: hats-reflect-session/v1
session_id: s1
timestamp: '2026-05-04T12:00:00+00:00'
hypothesis_verdicts:
  - hyp_id: HYP-001
    verdict: maybe
    evidence: x
    recommendation: keep
proposal_actions: []
self_problems: []
---
""")
    fake = FakeSubAgentRunner(p, [bad, bad])
    runner = ReflectSessionRunner(p, subagent_runner=fake)
    with pytest.raises(ReflectSessionError):
        runner.run("s1")

    proposals = list((p / ".agent" / "backlog" / "proposals").glob("PROP-*.yaml"))
    assert len(proposals) == 1


def test_retry_recovers_from_first_bad_output(tmp_path: Path):
    """First attempt invalid; second valid → no meta-proposal, retro saved."""
    p = _project(tmp_path)
    _make_session(p, "s1")
    _make_hyp(p, "HYP-001")
    bad = _wrap("---\nschema: hats-reflect-session/v1\nbroken")
    good = _wrap(_valid_reflect_retro("s1", ["HYP-001"]))
    fake = FakeSubAgentRunner(p, [bad, good])
    runner = ReflectSessionRunner(p, subagent_runner=fake)

    path = runner.run("s1")
    assert path.exists()
    assert len(fake.calls) == 2
    proposals = list((p / ".agent" / "backlog" / "proposals").glob("PROP-*.yaml"))
    assert proposals == []


def test_meta_proposal_increments_id_when_inbox_not_empty(tmp_path: Path):
    """Existing PROP in inbox → new meta-proposal gets next sequential id."""
    p = _project(tmp_path)
    _make_session(p, "s1")
    _make_hyp(p, "HYP-001")
    # Pre-existing proposal
    existing = {
        "id": "PROP-005",
        "created": "2026-05-04T00:00:00+00:00",
        "title": "x",
        "category": "code",
        "target": "y",
        "description": "d",
        "rationale": "r",
        "votes": [],
        "status": "open",
    }
    (p / ".agent" / "backlog" / "proposals" / "PROP-005.yaml").write_text(
        yaml.safe_dump(existing)
    )
    fake = FakeSubAgentRunner(p, ["", ""])
    runner = ReflectSessionRunner(p, subagent_runner=fake)

    with pytest.raises(ReflectSessionError):
        runner.run("s1")

    new_props = sorted(
        (p / ".agent" / "backlog" / "proposals").glob("PROP-*.yaml")
    )
    assert [pp.name for pp in new_props] == ["PROP-005.yaml", "PROP-006.yaml"]


# ------------------- LLM filed self_problem (still well-formed) -------------------

def test_self_problems_field_pass_through(tmp_path: Path):
    """LLM filed its own meta-proposal and listed the PROP-id in self_problems.

    Output is well-formed; runtime doesn't add another meta-proposal.
    """
    p = _project(tmp_path)
    _make_session(p, "s1")
    _make_hyp(p, "HYP-001")
    # LLM created a meta-proposal via CLI before emitting output
    meta = {
        "id": "PROP-001",
        "created": "2026-05-04T00:00:00+00:00",
        "title": "self-problem",
        "category": "process",
        "target": "reflect-session",
        "description": "I struggled with X",
        "rationale": "format ambiguous",
        "votes": [],
        "status": "open",
        "failed_session_id": "s1",
    }
    (p / ".agent" / "backlog" / "proposals" / "PROP-001.yaml").write_text(
        yaml.safe_dump(meta)
    )
    out = _wrap("""---
schema: hats-reflect-session/v1
session_id: s1
timestamp: '2026-05-04T12:00:00+00:00'
hypothesis_verdicts:
  - hyp_id: HYP-001
    verdict: n/a
    evidence: see PROP-001
    recommendation: keep
proposal_actions: []
self_problems:
  - PROP-001
---
""")
    fake = FakeSubAgentRunner(p, [out])
    runner = ReflectSessionRunner(p, subagent_runner=fake)

    path = runner.run("s1")
    assert path.exists()
    # No NEW proposal was added (still just PROP-001 from LLM)
    new_props = sorted(
        (p / ".agent" / "backlog" / "proposals").glob("PROP-*.yaml")
    )
    assert [pp.name for pp in new_props] == ["PROP-001.yaml"]
