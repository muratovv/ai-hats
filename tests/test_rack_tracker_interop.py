"""Rack-API ↔ tracker-shim interop on migrated HYP cards (HATS-1044 R1/R5).

The compat shim aligns its per-card lock with the rack kernel's
``<catalog>/<ID>/.lock`` by path convention (no import). This is the plan's
explicit concurrency test: a rack-API write and a tracker-CLI (shim) write on the
SAME migrated card contend on ONE lock file, so neither loses an update.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

import yaml

from ai_hats_rack import Workspace
from ai_hats_rack.migration import migrate_catalog
from ai_hats_rack.resolver import RackRoot
from ai_hats_tracker.hypothesis import HypothesisStore, ValidationLogEntry


def _project(tmp_path: Path) -> tuple[Path, Path]:
    ai = tmp_path / ".agent" / "ai-hats"
    hyp_catalog = ai / "tracker" / "hypotheses"
    hyp_catalog.mkdir(parents=True)
    (hyp_catalog / "HYP-001.yaml").write_text(
        yaml.safe_dump({
            "id": "HYP-001", "title": "t", "status": "active", "created": "2026-01-01",
            "source_task": "HATS-001", "hypothesis": "h", "validation_log": [],
        })
    )
    migrate_catalog(hyp_catalog, "hypotheses")
    return tmp_path, hyp_catalog


def _workspace(project_dir: Path) -> Workspace:
    root = RackRoot(
        project_dir=project_dir,
        tasks_dir=project_dir / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks",
    )
    return Workspace.discover([root])


def test_lock_paths_coincide(tmp_path: Path):
    project_dir, hyp_catalog = _project(tmp_path)
    ws = _workspace(project_dir)
    rack_lock = ws.kernel_for("HYP-001")._task_lock("HYP-001").lock_file
    from ai_hats_tracker.hypothesis.io import _lock_for

    shim_lock = _lock_for(HypothesisStore(hyp_catalog).path("HYP-001")).lock_file
    assert Path(rack_lock) == Path(shim_lock) == hyp_catalog / "HYP-001" / ".lock"


def test_interleaved_rack_and_shim_appends_no_lost_updates(tmp_path: Path):
    project_dir, hyp_catalog = _project(tmp_path)
    ws = _workspace(project_dir)
    store = HypothesisStore(hyp_catalog)
    errors: list[Exception] = []
    n_each = 10

    def rack_worker(i: int):
        try:
            ws.extension("hyp-verdicts").append_verdict(
                "HYP-001",
                {"date": "2026-05-04", "verdict": "inconclusive", "evidence": f"rack{i}",
                 "session_id": f"r{i}"},
                actor="rack:test",
                caller_cwd=project_dir,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def shim_worker(i: int):
        try:
            store.append_verdict(
                "HYP-001",
                ValidationLogEntry(date=date(2026, 5, 4), verdict="inconclusive", evidence=f"shim{i}"),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = []
    for i in range(n_each):
        threads.append(threading.Thread(target=rack_worker, args=(i,)))
        threads.append(threading.Thread(target=shim_worker, args=(i,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    raw = yaml.safe_load((hyp_catalog / "HYP-001" / "task.yaml").read_text())
    log = raw["validation_log"]
    assert len(log) == 2 * n_each  # no lost updates across the two writer worlds
    evidences = {e["evidence"] for e in log}
    assert evidences == {f"rack{i}" for i in range(n_each)} | {f"shim{i}" for i in range(n_each)}
    # The rack card shape survived every shim write.
    assert raw["state"] == "active"
    assert raw["links"]["source_task"] == ["HATS-001"]


def test_rack_write_visible_to_shim_read(tmp_path: Path):
    project_dir, hyp_catalog = _project(tmp_path)
    ws = _workspace(project_dir)
    ws.extension("hyp-verdicts").append_verdict(
        "HYP-001",
        {"date": "2026-05-04", "verdict": "refuted", "evidence": "e", "session_id": "s1"},
        actor="rack:test",
        caller_cwd=project_dir,
    )
    h = HypothesisStore(hyp_catalog).load("HYP-001")
    assert h.status == "active"
    assert [e.verdict for e in h.validation_log] == ["refuted"]
