"""Migration tool tests (HATS-1044 R5): flat → dir-per-card, dry-run, inventory
diff, idempotency, purge-source, and rack-readability of migrated cards."""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_hats_rack import Workspace
from ai_hats_rack.migrate import migrate_catalog, migrate_tracker
from ai_hats_rack.models import TaskCard
from ai_hats_rack.resolver import RackRoot


def _write_hyp(catalog: Path, hyp_id: str, **extra) -> Path:
    catalog.mkdir(parents=True, exist_ok=True)
    body = {
        "id": hyp_id,
        "title": f"t-{hyp_id}",
        "status": "active",
        "created": "2026-01-01",
        "source_task": "HATS-001",
        "hypothesis": "h",
        "validation_log": [],
        **extra,
    }
    p = catalog / f"{hyp_id}.yaml"
    p.write_text(yaml.safe_dump(body))
    return p


def _write_prop(catalog: Path, prop_id: str, **extra) -> Path:
    catalog.mkdir(parents=True, exist_ok=True)
    body = {
        "id": prop_id,
        "created": "2026-01-01T00:00:00Z",
        "title": f"t-{prop_id}",
        "category": "rule",
        "target": "x",
        "description": "d",
        "rationale": "r",
        "related_hypotheses": [],
        "votes": [],
        "status": "open",
        **extra,
    }
    p = catalog / f"{prop_id}.yaml"
    p.write_text(yaml.safe_dump(body))
    return p


def test_migrate_catalog_writes_dir_per_card_and_seeds_backlog(tmp_path: Path):
    cat = tmp_path / "hypotheses"
    _write_hyp(cat, "HYP-001", supersedes="HYP-000")
    report = migrate_catalog(cat, "hypotheses")

    assert report.migrated_ids == {"HYP-001"}
    assert not report.mismatches
    assert (cat / "backlog.yaml").is_file()
    task = yaml.safe_load((cat / "HYP-001" / "task.yaml").read_text())
    assert task["state"] == "active"  # status → state
    assert "status" not in task
    assert task["links"]["source_task"] == ["HATS-001"]  # scalar → single-element list
    assert task["links"]["supersedes"] == ["HYP-000"]
    assert task["hypothesis"] == "h"
    # The flat source is NEVER deleted by default (supervisor decides at the gate).
    assert (cat / "HYP-001.yaml").is_file()


def test_migrate_prop_moves_related_hypotheses_to_links(tmp_path: Path):
    cat = tmp_path / "proposals"
    _write_prop(cat, "PROP-001", related_hypotheses=["HYP-009", "HYP-010"], failed_session_id="s1")
    migrate_catalog(cat, "proposals")
    task = yaml.safe_load((cat / "PROP-001" / "task.yaml").read_text())
    assert task["state"] == "open"
    assert task["links"]["related_hypotheses"] == ["HYP-009", "HYP-010"]
    assert task["failed_session_id"] == "s1"


def test_dry_run_writes_nothing(tmp_path: Path):
    cat = tmp_path / "hypotheses"
    _write_hyp(cat, "HYP-001")
    report = migrate_catalog(cat, "hypotheses", dry_run=True)
    assert report.migrated_ids == {"HYP-001"}
    assert not (cat / "HYP-001").exists()
    assert not (cat / "backlog.yaml").exists()


def test_idempotent_rerun_skips(tmp_path: Path):
    cat = tmp_path / "hypotheses"
    _write_hyp(cat, "HYP-001")
    migrate_catalog(cat, "hypotheses")
    again = migrate_catalog(cat, "hypotheses")
    assert again.migrated_ids == set()
    assert [c.outcome for c in again.cards] == ["skipped"]
    assert not again.backlog_written  # backlog.yaml already identical


def test_inventory_diff_round_trip_clean(tmp_path: Path):
    cat = tmp_path / "hypotheses"
    _write_hyp(cat, "HYP-001")
    _write_hyp(cat, "HYP-002", status="confirmed", rollback_condition="revert")
    report = migrate_catalog(cat, "hypotheses")
    assert report.source_ids == {"HYP-001", "HYP-002"}
    assert not report.mismatches  # every card round-trips losslessly


def test_purge_source_removes_flat_after_clean_migration(tmp_path: Path):
    cat = tmp_path / "hypotheses"
    _write_hyp(cat, "HYP-001")
    migrate_catalog(cat, "hypotheses", purge_source=True)
    assert not (cat / "HYP-001.yaml").exists()
    assert (cat / "HYP-001" / "task.yaml").is_file()


def test_migrate_tracker_both_catalogs(tmp_path: Path):
    ai = tmp_path / ".agent" / "ai-hats"
    _write_hyp(ai / "tracker" / "hypotheses", "HYP-001")
    _write_prop(ai / "tracker" / "backlog" / "proposals", "PROP-001")
    report = migrate_tracker(ai)
    assert report.ok
    names = {c.name for c in report.catalogs}
    assert names == {"hypotheses", "proposals"}


def test_migrated_cards_are_rack_readable_and_discovered(tmp_path: Path):
    ai = tmp_path / ".agent" / "ai-hats"
    _write_hyp(ai / "tracker" / "hypotheses", "HYP-001")
    _write_prop(ai / "tracker" / "backlog" / "proposals", "PROP-001")
    migrate_tracker(ai)

    root = RackRoot(project_dir=tmp_path, tasks_dir=ai / "tracker" / "backlog" / "tasks")
    ws = Workspace.discover([root])
    assert {i.name for i in ws.instances} == {"tasks", "hypotheses", "proposals"}
    assert ws.exists("HYP-001") and ws.exists("PROP-001")
    card = ws.kernel_for("HYP-001").get("HYP-001")
    assert isinstance(card, TaskCard) and card.state == "active"


def test_migrate_catalog_target_split_leaves_flat_source(tmp_path: Path):
    # HATS-1054: dir-cards + backlog.yaml land in a SEPARATE target; the flat source
    # dir keeps its files and gets NO backlog.yaml (so discover does not mount it).
    source = tmp_path / "tracker" / "hypotheses"
    target = tmp_path / "tracker" / "backlog" / "hypotheses"
    _write_hyp(source, "HYP-001")
    report = migrate_catalog(source, "hypotheses", target_catalog=target)

    assert report.migrated_ids == {"HYP-001"}
    assert (target / "HYP-001" / "task.yaml").is_file()
    assert (target / "backlog.yaml").is_file()
    assert (source / "HYP-001.yaml").is_file()  # flat source untouched
    assert not (source / "backlog.yaml").exists()  # seed only in the target catalog
    assert not (source / "HYP-001").exists()  # no dir-card written into the source


def test_migrate_tracker_default_normalizes_hypotheses(tmp_path: Path):
    ai = tmp_path / ".agent" / "ai-hats"
    _write_hyp(ai / "tracker" / "hypotheses", "HYP-001")
    _write_prop(ai / "tracker" / "backlog" / "proposals", "PROP-001")
    report = migrate_tracker(ai)
    assert report.ok
    # Hypotheses normalized to the NEW catalog; proposals migrated in-place.
    assert (ai / "tracker" / "backlog" / "hypotheses" / "HYP-001" / "task.yaml").is_file()
    assert (ai / "tracker" / "backlog" / "hypotheses" / "backlog.yaml").is_file()
    assert (ai / "tracker" / "hypotheses" / "HYP-001.yaml").is_file()  # flat source stays
    assert not (ai / "tracker" / "hypotheses" / "backlog.yaml").exists()
    assert (ai / "tracker" / "backlog" / "proposals" / "PROP-001" / "task.yaml").is_file()


def test_migrate_tracker_discovers_normalized_catalogs(tmp_path: Path):
    ai = tmp_path / ".agent" / "ai-hats"
    _write_hyp(ai / "tracker" / "hypotheses", "HYP-001")
    _write_prop(ai / "tracker" / "backlog" / "proposals", "PROP-001")
    migrate_tracker(ai)
    root = RackRoot(project_dir=tmp_path, tasks_dir=ai / "tracker" / "backlog" / "tasks")
    ws = Workspace.discover([root])
    # Both normalized catalogs mount exactly once (no duplicate HYP prefix from the
    # old flat dir, which carries no backlog.yaml).
    assert {i.name for i in ws.instances} == {"tasks", "hypotheses", "proposals"}
    assert ws.exists("HYP-001") and ws.exists("PROP-001")


def test_dry_run_report_names_the_new_target(tmp_path: Path):
    ai = tmp_path / ".agent" / "ai-hats"
    _write_hyp(ai / "tracker" / "hypotheses", "HYP-001")
    report = migrate_tracker(ai, dry_run=True)
    rendered = report.render()
    assert str(ai / "tracker" / "backlog" / "hypotheses") in rendered
    assert str(ai / "tracker" / "hypotheses") in rendered  # names the flat source too
    assert not (ai / "tracker" / "backlog" / "hypotheses").exists()  # dry-run writes nothing


def test_hypotheses_target_override(tmp_path: Path):
    ai = tmp_path / ".agent" / "ai-hats"
    _write_hyp(ai / "tracker" / "hypotheses", "HYP-001")
    migrate_tracker(ai, hypotheses_target=Path("custom") / "hyps")
    assert (ai / "custom" / "hyps" / "HYP-001" / "task.yaml").is_file()
    assert (ai / "custom" / "hyps" / "backlog.yaml").is_file()


def test_flat_and_dir_card_coexist_glob_reads_dir_only(tmp_path: Path):
    # Coexistence: the flat file stays; rack's `*/task.yaml` scan ignores it.
    cat = tmp_path / "hypotheses"
    _write_hyp(cat, "HYP-001")
    migrate_catalog(cat, "hypotheses")
    dir_cards = list(cat.glob("*/task.yaml"))
    assert len(dir_cards) == 1  # one dir-card, the flat HYP-001.yaml is not matched
