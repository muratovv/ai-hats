"""Dual-layout (dir-per-card) tests for the HATS-1044 compat shim.

Rack-free (the tracker package must not import the rack): the migrated
``<dir>/<ID>/task.yaml`` cards are hand-written in the rack shape (``state`` not
``status``, link kinds under a ``links`` map), and a ``backlog.yaml`` marks the
catalog dir-mode. Asserts the shim translates on load/list/append/set/create and
that its per-card lock aligns with the rack's ``<dir>/<ID>/.lock`` path.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

import yaml

from ai_hats_tracker.hypothesis import (
    HypothesisStore,
    Proposal,
    ProposalStore,
    ValidationLogEntry,
    next_hypothesis_id,
    next_proposal_id,
)
from ai_hats_tracker.hypothesis.io import _lock_for


def _dir_hyp(catalog: Path, hyp_id: str, **task) -> Path:
    """Hand-write a migrated (rack-shape) HYP dir-card + mark the catalog dir-mode."""
    (catalog / "backlog.yaml").parent.mkdir(parents=True, exist_ok=True)
    (catalog / "backlog.yaml").write_text("name: hypotheses\nprefix: HYP\n")
    body = {
        "id": hyp_id,
        "title": f"t-{hyp_id}",
        "state": "active",
        "created": "2026-01-01",
        "hypothesis": "h",
        "validation_log": [],
        "links": {"source_task": ["HATS-001"]},
        **task,
    }
    p = catalog / hyp_id / "task.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(body))
    return p


def _dir_prop(catalog: Path, prop_id: str, **task) -> Path:
    (catalog / "backlog.yaml").parent.mkdir(parents=True, exist_ok=True)
    (catalog / "backlog.yaml").write_text("name: proposals\nprefix: PROP\n")
    body = {
        "id": prop_id,
        "title": f"t-{prop_id}",
        "state": "open",
        "created": "2026-01-01T00:00:00Z",
        "category": "rule",
        "target": "x",
        "description": "d",
        "rationale": "r",
        "links": {"related_hypotheses": ["HYP-009"]},
        "priority": "medium",  # rack anchor noise — must be dropped from the model view
        **task,
    }
    p = catalog / prop_id / "task.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(body))
    return p


class TestHypDualLayout:
    def test_load_translates_state_and_links(self, tmp_path: Path):
        _dir_hyp(tmp_path, "HYP-001", state="confirmed", closed="2026-06-01T10:00:00Z")
        h = HypothesisStore(tmp_path).load("HYP-001")
        assert h.status == "confirmed"  # state → status
        assert h.source_task == "HATS-001"  # links → scalar
        assert h.closed == date(2026, 6, 1)  # rack timestamp trimmed to a date

    def test_path_and_lock_align_with_rack(self, tmp_path: Path):
        _dir_hyp(tmp_path, "HYP-001")
        store = HypothesisStore(tmp_path)
        assert store.path("HYP-001") == tmp_path / "HYP-001" / "task.yaml"
        # The shim locks the rack's `<dir>/<ID>/.lock`, NOT `<file>.lock`.
        assert Path(_lock_for(store.path("HYP-001")).lock_file) == tmp_path / "HYP-001" / ".lock"

    def test_list_all_dedupes_dir_shadows_flat(self, tmp_path: Path):
        _dir_hyp(tmp_path, "HYP-001", state="active")
        # A stale flat file for the SAME id must be shadowed by the dir-card.
        (tmp_path / "HYP-001.yaml").write_text(
            yaml.safe_dump({"id": "HYP-001", "title": "stale", "status": "refuted",
                            "created": "2026-01-01", "source_task": "HATS-001", "hypothesis": "h"})
        )
        # A flat-only card with a different id still lists.
        (tmp_path / "HYP-002.yaml").write_text(
            yaml.safe_dump({"id": "HYP-002", "title": "flat", "status": "active",
                            "created": "2026-01-01", "source_task": "HATS-002", "hypothesis": "h"})
        )
        store = HypothesisStore(tmp_path)
        by_id = {h.id: h for h in store.list_all()}
        assert set(by_id) == {"HYP-001", "HYP-002"}
        assert by_id["HYP-001"].status == "active"  # dir-card won, not the stale flat "refuted"

    def test_append_verdict_preserves_rack_shape(self, tmp_path: Path):
        _dir_hyp(tmp_path, "HYP-001")
        store = HypothesisStore(tmp_path)
        store.append_verdict(
            "HYP-001",
            ValidationLogEntry(date=date(2026, 5, 4), verdict="refuted", evidence="gone", session_id="s1"),
        )
        raw = yaml.safe_load((tmp_path / "HYP-001" / "task.yaml").read_text())
        assert raw["state"] == "active" and raw["links"]["source_task"] == ["HATS-001"]
        assert len(raw["validation_log"]) == 1 and raw["validation_log"][0]["verdict"] == "refuted"

    def test_set_status_writes_state_key(self, tmp_path: Path):
        _dir_hyp(tmp_path, "HYP-001")
        HypothesisStore(tmp_path).set_status("HYP-001", "stalled")
        raw = yaml.safe_load((tmp_path / "HYP-001" / "task.yaml").read_text())
        assert raw["state"] == "stalled" and "status" not in raw

    def test_append_then_set_status_only_if_status(self, tmp_path: Path):
        _dir_hyp(tmp_path, "HYP-001", state="refuted")
        store = HypothesisStore(tmp_path)
        entry = ValidationLogEntry(date=date(2026, 5, 4), verdict="refuted", evidence="x")
        # Guard reads the rack `state` key: already refuted → no-op → None.
        assert store.append_then_set_status("HYP-001", entry, status="refuted", only_if_status="active") is None

    def test_create_dir_mode_writes_rack_shape(self, tmp_path: Path):
        # Seed dir-mode via a stub backlog.yaml + one dir-card, then create a new one.
        _dir_hyp(tmp_path, "HYP-001")
        store = HypothesisStore(tmp_path)
        from ai_hats_tracker.hypothesis import Hypothesis

        store.create(Hypothesis(
            id="HYP-002", title="new", status="active", created=date(2026, 1, 2),
            source_task="HATS-050", hypothesis="hh",
        ))
        raw = yaml.safe_load((tmp_path / "HYP-002" / "task.yaml").read_text())
        assert raw["state"] == "active" and "status" not in raw
        assert raw["links"]["source_task"] == ["HATS-050"]

    def test_next_id_spans_flat_and_dir(self, tmp_path: Path):
        _dir_hyp(tmp_path, "HYP-005")
        (tmp_path / "HYP-003.yaml").write_text("id: HYP-003\n")
        assert next_hypothesis_id(tmp_path) == "HYP-006"


class TestPropDualLayout:
    def test_load_filters_and_translates(self, tmp_path: Path):
        _dir_prop(tmp_path, "PROP-001", state="accepted", votes=[
            {"session_id": "s1", "timestamp": "2026-01-01T00:00:00Z", "reasoning": "ok"}
        ])
        p = ProposalStore(tmp_path).load("PROP-001")
        assert isinstance(p, Proposal)  # extra=forbid model built despite rack anchors
        assert p.status == "accepted"
        assert p.related_hypotheses == ["HYP-009"]
        assert len(p.votes) == 1

    def test_filter_and_set_status(self, tmp_path: Path):
        _dir_prop(tmp_path, "PROP-001")
        store = ProposalStore(tmp_path)
        assert [p.id for p in store.filter(status="open")] == ["PROP-001"]
        store.set_status("PROP-001", "rejected")
        raw = yaml.safe_load((tmp_path / "PROP-001" / "task.yaml").read_text())
        assert raw["state"] == "rejected"

    def test_next_proposal_id_spans_layouts(self, tmp_path: Path):
        _dir_prop(tmp_path, "PROP-002")
        assert next_proposal_id(tmp_path) == "PROP-003"


def test_flat_layout_unchanged(tmp_path: Path):
    """No backlog.yaml → legacy flat behavior is byte-for-byte unchanged."""
    (tmp_path / "HYP-001.yaml").write_text(
        yaml.safe_dump({"id": "HYP-001", "title": "t", "status": "active",
                        "created": "2026-01-01", "source_task": "HATS-001", "hypothesis": "h",
                        "validation_log": []})
    )
    store = HypothesisStore(tmp_path)
    assert store.path("HYP-001") == tmp_path / "HYP-001.yaml"
    assert str(_lock_for(store.path("HYP-001")).lock_file) == str(tmp_path / "HYP-001.yaml") + ".lock"
    store.append_verdict(
        "HYP-001",
        ValidationLogEntry(date=date(2026, 5, 4), verdict="confirmed", evidence="e"),
    )
    raw = yaml.safe_load((tmp_path / "HYP-001.yaml").read_text())
    assert raw["status"] == "active"  # flat still uses `status`


def test_concurrent_shim_appends_on_dir_card_no_loss(tmp_path: Path):
    _dir_hyp(tmp_path, "HYP-001")
    store = HypothesisStore(tmp_path)
    errors: list[Exception] = []

    def worker(i: int):
        try:
            store.append_verdict(
                "HYP-001",
                ValidationLogEntry(date=date(2026, 5, 4), verdict="inconclusive", evidence=f"e{i}"),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    raw = yaml.safe_load((tmp_path / "HYP-001" / "task.yaml").read_text())
    assert len(raw["validation_log"]) == 20
    assert raw["state"] == "active"  # concurrent appends preserved the rack shape
