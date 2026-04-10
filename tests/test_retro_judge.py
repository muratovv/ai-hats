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
    # Create a minimal session retro so _ensure_session_retro doesn't
    # attempt LLM generation (which requires a real provider CLI).
    retro_dir = project / ".agent" / "retrospectives" / "sessions" / "programmatic"
    retro_dir.mkdir(parents=True, exist_ok=True)
    (retro_dir / f"{session_id}.md").write_text(
        f"---\nschema: hats-session-retro/v1\nsession_id: {session_id}\n"
        f"project: test\nrole: test\ndate: '2026-04-08'\n"
        f"metrics: {{exit_code: 0, turns: 1, tool_calls: 2}}\n"
        f"summary: test session\n---\n# Retro\n"
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


def _valid_judge_retro(
    bundle_id: str,
    *,
    evidence_session_id: str = "20260408-101010-1",
    date_str: str = "2026-04-08",
) -> str:
    """Produce a minimal valid hats-judge-retro/v1 markdown document.

    `evidence_session_id` must be in the bundle being judged or the new
    integrity check (HATS-066) will reject it.
    """
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
      - session_id: {evidence_session_id}
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
    bm = BundleManager(project)
    # Pre-create with a fixed date so the bundle_id is deterministic.
    # _resolve_bundle's idempotency will find this bundle on any system date.
    bundle = bm.create(
        ["20260408-101010-1", "20260408-111111-1"],
        now=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
    )
    transcript = _wrap_in_delimiters(_valid_judge_retro(bundle.bundle_id))
    fake = FakeSubAgentRunner(project, [transcript])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)

    runner.judge(session_ids=["20260408-101010-1", "20260408-111111-1"])

    bundles = bm.list()
    assert len(bundles) == 1
    assert bundles[0].session_ids == ["20260408-101010-1", "20260408-111111-1"]


def test_judge_auto_creates_bundle_from_last_n(project: Path) -> None:
    for sid in ("20260408-101010-1", "20260408-111111-1", "20260408-121212-1"):
        _make_session(project, sid)
    bm = BundleManager(project)
    # Pre-create the bundle with a fixed date for deterministic ID.
    # Last 2 sessions are [111111, 121212]; idempotency will match.
    bundle = bm.create(
        ["20260408-111111-1", "20260408-121212-1"],
        now=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
    )
    # Evidence must reference one of the bundle's sessions.
    transcript = _wrap_in_delimiters(
        _valid_judge_retro(
            bundle.bundle_id,
            evidence_session_id="20260408-111111-1",
        )
    )
    fake = FakeSubAgentRunner(project, [transcript])
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


# --- HATS-066: integrity validation (post-schema semantic checks) ---


def test_judge_integrity_rejects_wrong_bundle_id_then_recovers(project: Path) -> None:
    """Schema-valid output with wrong bundle_id triggers retry with explicit hint."""
    _make_session(project, "20260408-101010-1")
    bm = BundleManager(project)
    bundle = bm.create(
        ["20260408-101010-1"],
        now=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
    )
    assert bundle.bundle_id == "BUNDLE-2026-04-08-001"

    # First attempt: wrong bundle_id (LLM hallucination) — schema passes,
    # integrity rejects.
    bad = _wrap_in_delimiters(_valid_judge_retro("BUNDLE-2026-04-08-002"))
    good = _wrap_in_delimiters(_valid_judge_retro(bundle.bundle_id))
    fake = FakeSubAgentRunner(project, [bad, good])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)

    path = runner.judge(bundle_id=bundle.bundle_id)
    assert path.exists()
    assert len(fake.calls) == 2
    # Retry prompt must echo the exact required bundle_id so the LLM can fix it.
    retry_task = fake.calls[1]["task"]
    assert bundle.bundle_id in retry_task
    assert "MUST be exactly" in retry_task


def test_judge_integrity_rejects_out_of_scope_evidence_then_recovers(project: Path) -> None:
    """Evidence pointing to a session not in the bundle triggers retry."""
    _make_session(project, "20260408-101010-1")
    _make_session(project, "20260408-202020-1")  # exists but NOT in bundle
    bm = BundleManager(project)
    bundle = bm.create(["20260408-101010-1"])

    bad = _wrap_in_delimiters(
        _valid_judge_retro(
            bundle.bundle_id,
            evidence_session_id="20260408-202020-1",  # out of bundle
        )
    )
    good = _wrap_in_delimiters(
        _valid_judge_retro(bundle.bundle_id, evidence_session_id="20260408-101010-1")
    )
    fake = FakeSubAgentRunner(project, [bad, good])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)

    path = runner.judge(bundle_id=bundle.bundle_id)
    assert path.exists()
    assert len(fake.calls) == 2
    # Retry prompt must list allowed session_ids and warn against scope creep.
    retry_task = fake.calls[1]["task"]
    assert "20260408-101010-1" in retry_task
    assert "scope is this bundle only" in retry_task


def test_judge_integrity_persistent_failure_raises(project: Path) -> None:
    """Both attempts violate integrity → JudgeValidationError with details."""
    _make_session(project, "20260408-101010-1")
    bm = BundleManager(project)
    bundle = bm.create(["20260408-101010-1"])

    bad1 = _wrap_in_delimiters(_valid_judge_retro("BUNDLE-2026-04-08-002"))
    bad2 = _wrap_in_delimiters(_valid_judge_retro("BUNDLE-2026-04-08-003"))
    fake = FakeSubAgentRunner(project, [bad1, bad2])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)

    with pytest.raises(JudgeValidationError, match="integrity"):
        runner.judge(bundle_id=bundle.bundle_id)
    assert len(fake.calls) == 2  # 1 original + 1 retry


def test_judge_integrity_check_via_validate_integrity_directly(project: Path) -> None:
    """Direct unit test of _validate_integrity to lock the violation message format."""
    from ai_hats.retro.judge import JudgeIntegrityError
    from ai_hats.retro.judge_retro import JudgeRetroV1

    _make_session(project, "20260408-101010-1")
    bm = BundleManager(project)
    bundle = bm.create(
        ["20260408-101010-1"],
        now=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
    )
    runner = JudgeRunner(project, subagent_runner=FakeSubAgentRunner(project, []), bundle_manager=bm)

    # Build a model with both violations: wrong bundle_id + out-of-scope evidence.
    bad_model = JudgeRetroV1.model_validate({
        "schema": "hats-judge-retro/v1",
        "judge_run_id": "x",
        "project": "ai-hats",
        "date": "2026-04-08",
        "bundle_id": "BUNDLE-2026-04-08-999",
        "findings": [{
            "id": "F1",
            "title": "x",
            "category": "process",
            "severity": "low",
            "root_cause": "rc",
            "evidence": [{
                "session_id": "session-not-in-bundle",
                "source": "audit",
                "location": "audit.md",
            }],
        }],
    })
    with pytest.raises(JudgeIntegrityError) as exc_info:
        runner._validate_integrity(bad_model, bundle)
    msg = str(exc_info.value)
    assert "bundle_id mismatch" in msg
    assert "BUNDLE-2026-04-08-999" in msg
    assert bundle.bundle_id in msg
    assert "session-not-in-bundle" in msg
    assert "F1" in msg


# --- HATS-110: _ensure_session_retro auto-generation ---


def _write_session_retro(project: Path, session_id: str, mode: str = "llm") -> Path:
    """Write a minimal session retro file and return its path."""
    retro_dir = project / ".agent" / "retrospectives" / "sessions" / mode
    retro_dir.mkdir(parents=True, exist_ok=True)
    path = retro_dir / f"{session_id}.md"
    path.write_text(
        f"---\nschema: hats-session-retro/v1\nsession_id: {session_id}\n"
        f"project: test\nrole: test\ndate: '2026-04-08'\n"
        f"metrics: {{exit_code: 0, turns: 1, tool_calls: 1}}\n"
        f"summary: test summary\n---\n# Retro\n"
    )
    return path


def test_ensure_session_retro_returns_existing(project: Path) -> None:
    """When retro already exists, _ensure_session_retro returns it without generating."""
    sid = "20260408-101010-1"
    _make_session(project, sid)
    existing = _write_session_retro(project, sid, mode="llm")

    bm = BundleManager(project)
    runner = JudgeRunner(project, subagent_runner=FakeSubAgentRunner(project, []), bundle_manager=bm)

    result = runner._ensure_session_retro(sid)
    assert result == existing


def test_ensure_session_retro_generates_programmatic_on_llm_failure(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When LLM generation fails, falls back to programmatic mode."""
    sid = "20260408-101010-1"
    _make_session(project, sid)
    # Also need a git repo for SessionRetroBuilder._git calls
    import subprocess
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)

    bm = BundleManager(project)
    runner = JudgeRunner(project, subagent_runner=FakeSubAgentRunner(project, []), bundle_manager=bm)

    # Patch SubprocessLLMCaller to always fail
    from ai_hats.retro.llm_caller import SubprocessLLMCaller

    def _boom_call(self, prompt):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(SubprocessLLMCaller, "__call__", _boom_call)

    result = runner._ensure_session_retro(sid)
    assert result is not None
    assert "/programmatic/" in str(result)
    assert result.exists()


def test_ensure_session_retro_returns_none_when_all_fail(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both LLM and programmatic fail, returns None."""
    sid = "20260408-999999-1"  # non-existent session dir
    # Don't create session dir → SessionRetroBuilder will raise FileNotFoundError

    bm = BundleManager(project)
    runner = JudgeRunner(project, subagent_runner=FakeSubAgentRunner(project, []), bundle_manager=bm)

    result = runner._ensure_session_retro(sid)
    assert result is None
