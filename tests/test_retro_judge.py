"""Tests for JudgeRunner — happy path, retry, validation, save."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_hats.retro.bundles import BundleManager
from ai_hats.retro.judge import (
    JUDGE_DELIM_END,
    JUDGE_DELIM_START,
    JudgeRunner,
    JudgeValidationError,
)
from ai_hats.retro.judge_retro import JudgeRetroV1
from ai_hats.retro.loader import load


# --- shared fixtures ---


def _make_session(project: Path, session_id: str) -> Path:
    sdir = project / ".gitlog" / f"session_{session_id}"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "audit.md").write_text(f"# Session Audit: {session_id}\n## Turn 1\nuser stuff\n")
    (sdir / "metrics.json").write_text(
        json.dumps({"role": "test", "turns": 1, "tool_calls": 2, "exit_code": 0})
    )
    return sdir


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / ".gitlog").mkdir()
    (tmp_path / ".agent" / "retrospectives" / "bundles").mkdir(parents=True)
    (tmp_path / ".agent" / "retrospectives" / "judge").mkdir(parents=True)
    return tmp_path


# --- FakeSubAgentRunner: pumps canned transcripts through real session dirs ---


class _FakeSession:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir


class FakeSubAgentRunner:
    """Drop-in replacement for SubAgentRunner that yields canned transcripts."""

    def __init__(self, project_dir: Path, transcripts: list[str]) -> None:
        self.project_dir = project_dir
        self._transcripts = list(transcripts)
        self.calls: list[dict] = []
        self._counter = 0

    def run(
        self,
        role_name: str,
        task: str = "",
        ticket_id: str = "",
        model: str = "",
        parent_session: str | None = None,
        isolation_mode: str = "discard",
    ) -> _FakeSession:
        self.calls.append(
            {
                "role_name": role_name,
                "task": task,
                "isolation_mode": isolation_mode,
            }
        )
        self._counter += 1
        sdir = self.project_dir / ".gitlog" / f"session_fake_{self._counter:04d}"
        sdir.mkdir(parents=True, exist_ok=True)
        if self._transcripts:
            (sdir / "transcript.txt").write_text(self._transcripts.pop(0))
        return _FakeSession(sdir)


# --- helpers ---


def _valid_judge_retro(bundle_id: str, date_str: str = "2026-04-08") -> str:
    """Produce a minimal valid hats-judge-retro/v1 markdown document."""
    return f"""---
schema: hats-judge-retro/v1
judge_run_id: judge-test-001
project: ai-hats
date: '{date_str}'
bundle_id: {bundle_id}
findings:
  - id: F1
    title: Example finding
    category: process
    severity: low
    root_cause: Example root cause
    evidence:
      - session_id: 20260408-101010-1
        source: audit
        location: audit.md:Turn 1
---

# Judge Retrospective

Body content here.
"""


def _wrap_in_delimiters(content: str, *, preamble: str = "", epilogue: str = "") -> str:
    return f"{preamble}\n{JUDGE_DELIM_START}\n{content}\n{JUDGE_DELIM_END}\n{epilogue}"


# --- tests ---


def test_judge_end_to_end_with_existing_bundle(project: Path) -> None:
    _make_session(project, "20260408-101010-1")
    bm = BundleManager(project)
    bundle = bm.create(
        ["20260408-101010-1"],
        now=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
    )
    transcript = _wrap_in_delimiters(
        _valid_judge_retro(bundle.bundle_id),
        preamble="Sure, here is the analysis.",
    )
    fake = FakeSubAgentRunner(project, [transcript])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)

    path = runner.judge(bundle_id=bundle.bundle_id)

    assert path.exists()
    assert path.parent == project / ".agent" / "retrospectives" / "judge"
    # roundtrip via loader
    loaded, body = load(path)
    assert isinstance(loaded, JudgeRetroV1)
    assert loaded.bundle_id == bundle.bundle_id
    # one call to the runner, role=judge
    assert len(fake.calls) == 1
    assert fake.calls[0]["role_name"] == "judge"


def test_judge_auto_creates_bundle_from_sessions(project: Path) -> None:
    _make_session(project, "20260408-101010-1")
    _make_session(project, "20260408-111111-1")
    transcript = _wrap_in_delimiters(_valid_judge_retro("BUNDLE-2026-04-08-001"))
    fake = FakeSubAgentRunner(project, [transcript])
    bm = BundleManager(project)
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)

    runner.judge(session_ids=["20260408-101010-1", "20260408-111111-1"])

    bundles = bm.list()
    assert len(bundles) == 1
    assert bundles[0].session_ids == ["20260408-101010-1", "20260408-111111-1"]


def test_judge_auto_creates_bundle_from_last_n(project: Path) -> None:
    for sid in ("20260408-101010-1", "20260408-111111-1", "20260408-121212-1"):
        _make_session(project, sid)
    transcript = _wrap_in_delimiters(_valid_judge_retro("BUNDLE-2026-04-08-001"))
    fake = FakeSubAgentRunner(project, [transcript])
    bm = BundleManager(project)
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)

    runner.judge(last_n=2)

    bundles = bm.list()
    assert len(bundles) == 1
    assert bundles[0].session_ids == ["20260408-111111-1", "20260408-121212-1"]


def test_judge_extracts_between_delimiters(project: Path) -> None:
    _make_session(project, "20260408-101010-1")
    bm = BundleManager(project)
    bundle = bm.create(["20260408-101010-1"])
    transcript = _wrap_in_delimiters(
        _valid_judge_retro(bundle.bundle_id),
        preamble="<lots of preamble noise>",
        epilogue="<trailing chatter that should be ignored>",
    )
    fake = FakeSubAgentRunner(project, [transcript])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)
    path = runner.judge(bundle_id=bundle.bundle_id)
    assert path.exists()


def test_judge_extracts_with_frontmatter_fallback(project: Path) -> None:
    """No delimiters, but frontmatter sniff finds the schema."""
    _make_session(project, "20260408-101010-1")
    bm = BundleManager(project)
    bundle = bm.create(["20260408-101010-1"])
    md = _valid_judge_retro(bundle.bundle_id)
    # No delimiter wrapping
    transcript = f"some preamble\n{md}\nepilogue"
    fake = FakeSubAgentRunner(project, [transcript])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)
    path = runner.judge(bundle_id=bundle.bundle_id)
    assert path.exists()


def test_judge_prompt_contains_focus_and_session_context(project: Path) -> None:
    """Focus is a judge-run parameter; it must reach the prompt without being
    stored on the bundle."""
    _make_session(project, "20260408-101010-1")
    bm = BundleManager(project)
    bundle = bm.create(["20260408-101010-1"])
    transcript = _wrap_in_delimiters(_valid_judge_retro(bundle.bundle_id))
    fake = FakeSubAgentRunner(project, [transcript])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)
    runner.judge(bundle_id=bundle.bundle_id, focus="git discipline lens")

    task = fake.calls[0]["task"]
    assert "git discipline lens" in task
    assert "20260408-101010-1" in task
    assert bundle.bundle_id in task
    assert JUDGE_DELIM_START in task


def test_judge_focus_not_persisted_in_bundle(project: Path) -> None:
    """Focus passed to judge does not modify the bundle on disk."""
    _make_session(project, "20260408-101010-1")
    bm = BundleManager(project)
    bundle = bm.create(["20260408-101010-1"])
    transcript = _wrap_in_delimiters(_valid_judge_retro(bundle.bundle_id))
    fake = FakeSubAgentRunner(project, [transcript])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)
    runner.judge(bundle_id=bundle.bundle_id, focus="ephemeral lens")

    reloaded = bm.get(bundle.bundle_id)
    # bundle has no focus field at all (schema rejects it)
    assert not hasattr(reloaded, "focus") or getattr(reloaded, "focus", None) is None


def test_judge_saves_with_daily_counter_filename(project: Path) -> None:
    _make_session(project, "20260408-101010-1")
    bm = BundleManager(project)
    bundle = bm.create(["20260408-101010-1"])

    # pre-existing judge file from same day
    (project / ".agent" / "retrospectives" / "judge" / "2026-04-08-judge-001.md").write_text(
        "placeholder"
    )

    transcript = _wrap_in_delimiters(_valid_judge_retro(bundle.bundle_id))
    fake = FakeSubAgentRunner(project, [transcript])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)
    path = runner.judge(bundle_id=bundle.bundle_id)
    assert path.name == "2026-04-08-judge-002.md"


def test_judge_resolve_requires_input(project: Path) -> None:
    bm = BundleManager(project)
    fake = FakeSubAgentRunner(project, [])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)
    with pytest.raises(ValueError, match="Must provide"):
        runner.judge()


def test_judge_extract_markdown_returns_empty_for_empty(project: Path) -> None:
    bm = BundleManager(project)
    runner = JudgeRunner(project, subagent_runner=FakeSubAgentRunner(project, []), bundle_manager=bm)
    assert runner._extract_markdown("") == ""
    assert runner._extract_markdown("no markers and no schema") == "no markers and no schema"


# --- retry path (Step 7) ---


def test_judge_validation_retry_path(project: Path) -> None:
    """First transcript invalid, second valid → exactly 2 calls, success."""
    _make_session(project, "20260408-101010-1")
    bm = BundleManager(project)
    bundle = bm.create(["20260408-101010-1"])

    invalid = _wrap_in_delimiters(
        """---
schema: hats-judge-retro/v1
project: ai-hats
date: '2026-04-08'
bundle_id: BUNDLE-2026-04-08-001
findings: []
---
"""
    )  # findings empty → ValidationError
    valid = _wrap_in_delimiters(_valid_judge_retro(bundle.bundle_id))
    fake = FakeSubAgentRunner(project, [invalid, valid])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)

    path = runner.judge(bundle_id=bundle.bundle_id)
    assert path.exists()
    assert len(fake.calls) == 2
    # second call's task is the correction prompt
    assert "previous attempt" in fake.calls[1]["task"].lower()


def test_judge_validation_failure_after_retry_raises(project: Path) -> None:
    _make_session(project, "20260408-101010-1")
    bm = BundleManager(project)
    bundle = bm.create(["20260408-101010-1"])

    invalid = _wrap_in_delimiters("---\nnot: valid\n---\n")
    fake = FakeSubAgentRunner(project, [invalid, invalid])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)

    with pytest.raises(JudgeValidationError, match="failed validation"):
        runner.judge(bundle_id=bundle.bundle_id)
    assert len(fake.calls) == 2  # 1 original + 1 retry
