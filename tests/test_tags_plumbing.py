"""Runtime plumbing for session tags (HATS-163).

Covers two paths:

1. Sub-agent — ``_finalize_sub_agent(..., tags=...)`` writes tags into
   metrics.json alongside exit_code/role/model.
2. Interactive — ``_finalize_session`` writes tags into its initial metrics
   dict, and crucially those tags must **survive** the subsequent
   ``AuditWriter._write_metrics()`` enrichment pass (which does
   ``existing.update({turns, tokens, ...})`` — a key-level merge that does
   not touch unrelated fields but is easy to break by accident).
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats.observe import AuditWriter, Session
from ai_hats.runtime import _finalize_sub_agent


def _make_session(tmp_path: Path, *, provider: str = "claude") -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    session = Session(session_id="test", session_dir=session_dir)
    session.init_audit(role="primary", provider=provider, model="")
    return session


# ---------------------------------------------------------------------------
# Sub-agent path — tags in metrics.json
# ---------------------------------------------------------------------------


def test_sub_agent_tags_land_in_metrics(tmp_path):
    session = _make_session(tmp_path)

    _finalize_sub_agent(
        session,
        role="diagnoser",
        model="sonnet",
        isolation_mode="discard",
        exit_code=0,
        stdout="ok\n",
        tags={"alert_fp": "abc123", "client": "home-lab"},
    )

    m = json.loads(session.metrics_path.read_text())
    assert m["tags"] == {"alert_fp": "abc123", "client": "home-lab"}
    # Existing fields preserved.
    assert m["exit_code"] == 0
    assert m["role"] == "diagnoser"


def test_sub_agent_empty_tags_omitted(tmp_path):
    """Empty dict and None both mean 'no tags' — the key must not appear in
    metrics.json (keeps the doc clean for sessions that don't tag)."""
    session = _make_session(tmp_path)

    _finalize_sub_agent(
        session,
        role="primary",
        model="",
        isolation_mode="discard",
        exit_code=0,
        tags={},
    )

    m = json.loads(session.metrics_path.read_text())
    assert "tags" not in m


def test_sub_agent_none_tags_omitted(tmp_path):
    session = _make_session(tmp_path)

    _finalize_sub_agent(
        session,
        role="primary",
        model="",
        isolation_mode="discard",
        exit_code=0,
        tags=None,
    )

    m = json.loads(session.metrics_path.read_text())
    assert "tags" not in m


def test_sub_agent_tags_preserved_on_timeout(tmp_path):
    """Tags must land even on early termination — orchestrator relies on them
    for post-hoc queries regardless of exit path."""
    session = _make_session(tmp_path)

    _finalize_sub_agent(
        session,
        role="diagnoser",
        model="",
        isolation_mode="discard",
        exit_code=124,
        stdout="partial\n",
        timed_out=True,
        tags={"alert_fp": "xyz"},
    )

    m = json.loads(session.metrics_path.read_text())
    assert m["timed_out"] is True
    assert m["tags"] == {"alert_fp": "xyz"}


# ---------------------------------------------------------------------------
# AuditWriter regression — tags survive enrichment
# ---------------------------------------------------------------------------


def test_tags_survive_auditwriter_enrichment(tmp_path):
    """Regression guard: WrapRunner writes tags into metrics.json at
    finalize_audit time, then AuditWriter._write_metrics() enriches with
    turns/tokens/models/tool_calls. Because the update is a shallow dict
    merge on selected keys, tags MUST be untouched. If someone later changes
    _write_metrics to overwrite the full dict, this test fails loudly.
    """
    session = _make_session(tmp_path)

    # 1. Initial finalize (simulates runtime._finalize_session payload).
    session.finalize_audit({
        "exit_code": 0,
        "role": "primary",
        "provider": "claude",
        "tags": {"experiment": "prompt-v2", "client": "acme"},
    })

    # 2. Enrichment pass (AuditWriter._write_metrics with no turns / empty stats).
    writer = AuditWriter()
    writer._write_metrics(session, turns=[], model_stats={}, agg_usage={})

    # 3. Tags survived.
    m = json.loads(session.metrics_path.read_text())
    assert m["tags"] == {"experiment": "prompt-v2", "client": "acme"}
    # Enrichment fields added.
    assert "turns" in m
    assert "tokens" in m
    assert m["tokens"] == {
        "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0,
    }
    # Original fields preserved too.
    assert m["exit_code"] == 0
    assert m["role"] == "primary"
