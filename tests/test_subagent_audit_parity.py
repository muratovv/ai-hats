"""SubAgent path acquires structured ``audit.md`` parity (HATS-535).

Pre-HATS-535 the SubAgent path's ``audit.md`` was meta-only (header +
composition, no turn markers) because ``_finalize_sub_agent`` never
called ``AuditWriter`` even though the SDK persisted the same JSONL
under ``~/.claude/projects/<key>/<claude_session_id>.jsonl`` that HITL
used. HATS-535 fixes the asymmetry: ``_finalize_sub_agent`` now
invokes the ``finalize-subagent`` sub-pipeline (which runs
``MakeAudit``) when a ``layout`` standing where the SDK ran and a
``claude_session_id`` are both known.

This test fakes a minimal JSONL with one user/assistant turn at the
expected ``~/.claude/projects/<cwd_key>/<csid>.jsonl`` path, calls
``_finalize_sub_agent`` with the SDK-path kwargs, and asserts that
``audit.md`` ends up containing the ``👤`` + ``👾`` turn markers that
only the JSONL→AuditWriter path produces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats_core.layout import ProjectLayout
from ai_hats_observe.artifacts import RETRO_LOG, session_dirname

from ai_hats.paths import claude_transcripts_dir
from ai_hats.surfaces.claude.provider import ClaudeSurface
from ai_hats_observe import AuditWriter, Session
from ai_hats.runtime import _finalize_sub_agent


@pytest.fixture(autouse=True)
def _claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude's record tree under tmp — through the env the resolver reads, not a patch."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "home" / ".claude"))


def _claude_dir_for(cwd: Path) -> Path:
    d = claude_transcripts_dir(cwd)
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
    jsonl_path.write_text(json.dumps(user_entry) + "\n" + json.dumps(asst_entry) + "\n")


def _project_session(main: Path, session_id: str = "test") -> Session:
    """A sub-agent's session lives in the PROJECT's run tree, never in its checkout."""
    session_dir = ProjectLayout.at(main).sessions.runs / session_dirname(session_id)
    session_dir.mkdir(parents=True)
    session = Session(session_id=session_id, session_dir=session_dir)
    session.init_audit(role="primary", provider="claude", model="claude-opus-4-7")
    return session


def _finalize(session: Session, layout: ProjectLayout, csid: str) -> None:
    _finalize_sub_agent(
        session,
        role="primary",
        model="claude-opus-4-7",
        isolation_mode="discard",
        exit_code=0,
        stdout="alpha",
        stderr="",
        extra_metrics={"claude_session_id": csid},
        layout=layout,
        # HATS-867: factories arrive injected (production: CompositionPayload).
        # HATS-1087: transcript_resolver too — production threads it via payload.
        session_factory=Session,
        audit_writer_factory=AuditWriter,
        transcript_resolver=ClaudeSurface().resolve_transcript,
    )


def test_subagent_audit_md_contains_user_and_assistant_markers(tmp_path):
    """End-to-end SubAgent parity: ``_finalize_sub_agent`` with a layout
    standing where the SDK ran + ``claude_session_id`` → ``audit.md`` carries
    ``👤`` + ``👾`` markers (the JSONL-derived structured audit).
    Pre-HATS-535 these were absent on the SubAgent path."""
    main = tmp_path / "main"
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    csid = "abc-test-uuid"
    _write_minimal_jsonl(
        _claude_dir_for(work_dir) / f"{csid}.jsonl",
        user_text="say alpha please",
        asst_text="alpha",
    )
    session = _project_session(main)

    _finalize(session, ProjectLayout.at(main).with_cwd(work_dir), csid)

    audit_text = session.audit_path.read_text()
    assert "👤 say alpha please" in audit_text, (
        f"HATS-535 parity regression: SubAgent audit.md missing user marker. Content:\n{audit_text}"
    )
    assert "👾 alpha" in audit_text, (
        f"HATS-535 parity regression: SubAgent audit.md missing "
        f"assistant marker. Content:\n{audit_text}"
    )


def test_the_transcript_is_found_under_the_checkouts_real_path(tmp_path):
    """The SDK keys its record by the REAL path of the dir it ran in — on macOS
    a ``/var/folders`` worktree is recorded under ``/private/var/folders``. A
    layout standing on the unresolved path used to miss it."""
    main = tmp_path / "main"
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    csid = "symlinked-uuid"
    _write_minimal_jsonl(
        _claude_dir_for(real.resolve()) / f"{csid}.jsonl",
        user_text="say beta please",
        asst_text="beta",
    )
    session = _project_session(main)

    _finalize(session, ProjectLayout.at(main).with_cwd(link), csid)

    assert "👾 beta" in session.audit_path.read_text()


def test_the_retro_log_lands_in_the_project_not_in_the_checkout(tmp_path):
    """``finalize-subagent`` also decides on the auto-retro. That decision was
    logged through a layout re-rooted on the sub-agent's checkout — a discard
    worktree — so ``retro.log`` was written into a tree about to be deleted."""
    main = tmp_path / "main"
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    csid = "retro-uuid"
    _write_minimal_jsonl(_claude_dir_for(work_dir) / f"{csid}.jsonl", "u", "a")
    session = _project_session(main)

    _finalize(session, ProjectLayout.at(main).with_cwd(work_dir), csid)

    assert (session.session_dir / RETRO_LOG).exists()
    assert not (work_dir / ".agent").exists()


def test_subagent_audit_md_unchanged_without_layout(tmp_path):
    """Backwards-compat: callers that pass no ``layout`` (legacy
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
        # layout intentionally omitted
    )

    audit_text = session.audit_path.read_text()
    # Meta-only audit: has Metrics block, no turn markers.
    assert "## Metrics" in audit_text
    assert "👤" not in audit_text
    assert "👾" not in audit_text


def test_subagent_audit_md_unchanged_without_claude_session_id(tmp_path):
    """Agy / legacy subprocess providers (no claude_session_id) keep
    producing meta-only audit.md."""
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    session = Session(session_id="test", session_dir=session_dir)
    session.init_audit(role="primary", provider="agy")

    _finalize_sub_agent(
        session,
        role="primary",
        model="agy-pro",
        isolation_mode="discard",
        exit_code=0,
        stdout="ok",
        layout=ProjectLayout.at(tmp_path),
        # extra_metrics omitted → no claude_session_id
    )

    audit_text = session.audit_path.read_text()
    assert "## Metrics" in audit_text
    assert "👤" not in audit_text
    assert "👾" not in audit_text
