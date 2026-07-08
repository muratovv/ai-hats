"""SubAgent path acquires structured ``audit.md`` parity (HATS-535).

Pre-HATS-535 the SubAgent path's ``audit.md`` was meta-only (header +
composition, no turn markers) because ``_finalize_sub_agent`` never
called ``AuditWriter`` even though the SDK persisted the same JSONL
under ``~/.claude/projects/<key>/<claude_session_id>.jsonl`` that HITL
used. HATS-535 fixes the asymmetry: ``_finalize_sub_agent`` now
invokes the ``finalize-subagent`` sub-pipeline (which runs
``MakeAudit``) when ``work_dir`` + ``claude_session_id`` are both
known.

This test fakes a minimal JSONL with one user/assistant turn at the
expected ``~/.claude/projects/<work_dir_key>/<csid>.jsonl`` path, calls
``_finalize_sub_agent`` with the SDK-path kwargs, and asserts that
``audit.md`` ends up containing the ``👤`` + ``👾`` turn markers that
only the JSONL→AuditWriter path produces.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats_observe import AuditWriter, Session
from ai_hats.runtime import _finalize_sub_agent


def _claude_dir_for(home: Path, work_dir: Path) -> Path:
    project_key = str(work_dir).replace("/", "-")
    d = home / ".claude" / "projects" / project_key
    d.mkdir(parents=True)
    return d


def _write_minimal_jsonl(jsonl_path: Path, user_text: str, asst_text: str) -> None:
    """Minimal valid claude jsonl: one user msg + one assistant msg.

    Schema matches what ``AuditWriter._parse_jsonl`` consumes:
    - ``type: user`` with ``message.content`` as a string
    - ``type: assistant`` with ``message.content`` as a list of
      ``{"type": "text", "text": "..."}`` blocks + ``message.model``
      + ``message.usage`` dict (token aggregation)
    """
    user_entry = {
        "type": "user",
        "timestamp": "2026-05-26T08:00:00Z",
        "message": {"content": user_text},
    }
    asst_entry = {
        "type": "assistant",
        "timestamp": "2026-05-26T08:00:05Z",
        "message": {
            "model": "claude-opus-4-7",
            "content": [{"type": "text", "text": asst_text}],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    }
    jsonl_path.write_text(
        json.dumps(user_entry) + "\n" + json.dumps(asst_entry) + "\n"
    )


def test_subagent_audit_md_contains_user_and_assistant_markers(
    tmp_path, monkeypatch,
):
    """End-to-end SubAgent parity: ``_finalize_sub_agent`` with
    ``work_dir`` + ``claude_session_id`` → ``audit.md`` carries
    ``👤`` + ``👾`` markers (the JSONL-derived structured audit).
    Pre-HATS-535 these were absent on the SubAgent path."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    work_dir = tmp_path / "work"
    work_dir.mkdir()

    # Fake claude jsonl at the path MakeAudit will look up via
    # _claude_jsonl_path(work_dir, csid) →
    # ~/.claude/projects/<work_dir_key>/<csid>.jsonl
    claude_dir = _claude_dir_for(tmp_path / "home", work_dir)
    csid = "abc-test-uuid"
    _write_minimal_jsonl(
        claude_dir / f"{csid}.jsonl",
        user_text="say alpha please",
        asst_text="alpha",
    )

    # Build a SubAgent-style Session (session_dir under work_dir/.agent).
    session_dir = work_dir / "session_test"
    session_dir.mkdir()
    session = Session(session_id="test", session_dir=session_dir)
    session.init_audit(role="primary", provider="claude", model="claude-opus-4-7")

    _finalize_sub_agent(
        session,
        role="primary",
        model="claude-opus-4-7",
        isolation_mode="discard",
        exit_code=0,
        stdout="alpha",
        stderr="",
        extra_metrics={"claude_session_id": csid},
        work_dir=work_dir,
        # HATS-867: factories arrive injected (production: CompositionPayload).
        session_factory=Session,
        audit_writer_factory=AuditWriter,
    )

    audit_text = session.audit_path.read_text()
    assert "👤 say alpha please" in audit_text, (
        f"HATS-535 parity regression: SubAgent audit.md missing user "
        f"marker. Content:\n{audit_text}"
    )
    assert "👾 alpha" in audit_text, (
        f"HATS-535 parity regression: SubAgent audit.md missing "
        f"assistant marker. Content:\n{audit_text}"
    )


def test_subagent_audit_md_unchanged_without_work_dir(tmp_path):
    """Backwards-compat: callers that don't pass ``work_dir`` (legacy
    subprocess providers) keep producing the pre-HATS-535 meta-only
    audit.md — no behaviour change."""
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    session = Session(session_id="test", session_dir=session_dir)
    session.init_audit(role="primary", provider="claude")

    _finalize_sub_agent(
        session,
        role="primary",
        model="haiku",
        isolation_mode="discard",
        exit_code=0,
        stdout="ok",
        extra_metrics={"claude_session_id": "csid-no-effect"},
        # work_dir intentionally omitted
    )

    audit_text = session.audit_path.read_text()
    # Meta-only audit: has Metrics block, no turn markers.
    assert "## Metrics" in audit_text
    assert "👤" not in audit_text
    assert "👾" not in audit_text


def test_subagent_audit_md_unchanged_without_claude_session_id(tmp_path):
    """Gemini / legacy subprocess providers (no claude_session_id) keep
    producing meta-only audit.md."""
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    session = Session(session_id="test", session_dir=session_dir)
    session.init_audit(role="primary", provider="gemini")

    _finalize_sub_agent(
        session,
        role="primary",
        model="gemini-pro",
        isolation_mode="discard",
        exit_code=0,
        stdout="ok",
        work_dir=tmp_path,
        # extra_metrics omitted → no claude_session_id
    )

    audit_text = session.audit_path.read_text()
    assert "## Metrics" in audit_text
    assert "👤" not in audit_text
    assert "👾" not in audit_text
