"""Doc store vertical slices: fs-as-truth view, frozen pins, rm policy (HATS-1021)."""

from __future__ import annotations

import hashlib

import pytest
import yaml

from ai_hats_rack.docstore import (
    DocStore,
    DocumentNameError,
    FrozenDocumentError,
    FrozenPinDriftError,
    UnknownDocumentError,
    compute_digest,
)
from ai_hats_rack.kernel import UnknownTaskError
from rack_testkit import make_kernel


@pytest.fixture
def store(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    kernel.create(actor="test", caller_cwd=cwd, title="doc host")
    return DocStore(tasks_dir)


def _card_dir(store):
    return store.card_dir("T-001")


def _card_yaml(store):
    return yaml.safe_load((_card_dir(store) / "task.yaml").read_text(encoding="utf-8"))


def _expected_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()[:12]


# ----- the ledger is a view: direct writes are visible immediately -----------


def test_direct_write_is_visible_immediately_with_correct_digest(store):
    # rev4 / baseline F3: writing the file IS the registration.
    payload = b"pytest tail\n"
    (_card_dir(store) / "gate.log").write_bytes(payload)
    docs = store.scan("T-001")
    assert [d.name for d in docs] == ["gate.log"]
    doc = docs[0]
    assert doc.path == (_card_dir(store) / "gate.log").absolute()
    assert doc.path.is_absolute()
    assert doc.digest == _expected_digest(payload)
    assert doc.size == len(payload)
    assert doc.mtime.endswith("Z")
    assert not doc.frozen and not doc.drift


def test_overwrite_updates_view_without_any_registration(store):
    path = _card_dir(store) / "notes.md"
    path.write_text("v1")
    first = store.scan("T-001")[0].digest
    path.write_text("v2 — longer body")
    second = store.scan("T-001")[0]
    assert second.digest != first
    assert second.digest == compute_digest(path)


def test_service_and_dotfiles_are_not_documents(store):
    card_dir = _card_dir(store)
    (card_dir / ".lock").write_text("")
    (card_dir / ".hidden").write_text("x")
    (card_dir / ".cache").mkdir()
    (card_dir / ".cache" / "blob").write_text("x")
    (card_dir / "real.md").write_text("doc")
    assert [d.name for d in store.scan("T-001")] == ["real.md"]


def test_nested_legacy_attachments_are_visible_as_relative_names(store):
    # epic v2 §4: legacy attachments/ blobs are simply seen by the scan.
    nested = _card_dir(store) / "attachments"
    nested.mkdir()
    (nested / "design.md").write_text("legacy blob")
    assert [d.name for d in store.scan("T-001")] == ["attachments/design.md"]


def test_scan_unknown_task_raises(store):
    with pytest.raises(UnknownTaskError):
        store.scan("T-404")


# ----- frozen pins ------------------------------------------------------------


def test_freeze_pins_name_digest_frozen_into_task_yaml(store):
    (_card_dir(store) / "evidence.log").write_bytes(b"evidence")
    info = store.freeze("T-001", "evidence.log", actor="tester")
    raw = _card_yaml(store)
    assert raw["documents"] == [
        {"name": "evidence.log", "digest": _expected_digest(b"evidence"), "frozen": True}
    ]
    assert info.frozen and info.pinned_digest == info.digest
    # the pin write is audited in work_log, transactionally with the save
    assert any("Froze document evidence.log" in e["message"] for e in raw["work_log"])


def test_freeze_is_idempotent_on_unchanged_content(store):
    (_card_dir(store) / "evidence.log").write_bytes(b"evidence")
    store.freeze("T-001", "evidence.log")
    updated_before = _card_yaml(store)["updated"]
    store.freeze("T-001", "evidence.log")
    raw = _card_yaml(store)
    assert len(raw["documents"]) == 1
    assert raw["updated"] == updated_before  # noop did not rewrite the card


def test_drift_under_pin_is_an_error_on_ls(store):
    path = _card_dir(store) / "evidence.log"
    path.write_bytes(b"evidence")
    store.freeze("T-001", "evidence.log")
    path.write_bytes(b"tampered")
    doc = store.scan("T-001")[0]
    assert doc.frozen and doc.drift == "modified"
    assert doc.pinned_digest == _expected_digest(b"evidence")
    assert doc.digest == _expected_digest(b"tampered")


def test_missing_pinned_file_surfaces_as_missing_drift(store):
    path = _card_dir(store) / "evidence.log"
    path.write_bytes(b"evidence")
    store.freeze("T-001", "evidence.log")
    path.unlink()
    doc = store.scan("T-001")[0]
    assert doc.frozen and doc.drift == "missing"
    assert doc.digest == "" and doc.mtime == ""


def test_refreeze_of_drifted_pin_is_refused_without_flag(store):
    path = _card_dir(store) / "evidence.log"
    path.write_bytes(b"evidence")
    store.freeze("T-001", "evidence.log")
    path.write_bytes(b"new evidence")
    with pytest.raises(FrozenPinDriftError) as err:
        store.freeze("T-001", "evidence.log")
    assert "--refreeze" in str(err.value)  # tiered escape hatch names its price
    # pin unchanged after the refusal
    assert _card_yaml(store)["documents"][0]["digest"] == _expected_digest(b"evidence")


def test_refreeze_with_flag_accepts_new_content(store):
    path = _card_dir(store) / "evidence.log"
    path.write_bytes(b"evidence")
    store.freeze("T-001", "evidence.log")
    path.write_bytes(b"new evidence")
    info = store.freeze("T-001", "evidence.log", refreeze=True)
    assert info.pinned_digest == _expected_digest(b"new evidence")
    assert _card_yaml(store)["documents"][0]["digest"] == _expected_digest(b"new evidence")
    assert not store.scan("T-001")[0].drift


def test_freeze_missing_file_raises_unknown_document(store):
    with pytest.raises(UnknownDocumentError):
        store.freeze("T-001", "ghost.md")


# ----- rm: delete policy ------------------------------------------------------


def test_rm_moves_file_to_trash_not_deletes(store):
    path = _card_dir(store) / "scratch.log"
    path.write_bytes(b"scratch data")
    result = store.remove("T-001", "scratch.log", actor="tester")
    assert not path.exists()
    assert result.trashed_to is not None and result.trashed_to.is_file()
    assert result.trashed_to.read_bytes() == b"scratch data"  # recoverable (HATS-470)
    assert not result.pin_removed
    raw = _card_yaml(store)
    assert any("Removed document scratch.log" in e["message"] for e in raw["work_log"])


def test_rm_frozen_requires_ack_flag(store):
    path = _card_dir(store) / "evidence.log"
    path.write_bytes(b"evidence")
    store.freeze("T-001", "evidence.log")
    with pytest.raises(FrozenDocumentError) as err:
        store.remove("T-001", "evidence.log")
    assert "--ack-frozen" in str(err.value)
    assert path.exists()  # refusal changed nothing


def test_rm_frozen_with_ack_removes_pin_and_trashes_file(store):
    path = _card_dir(store) / "evidence.log"
    path.write_bytes(b"evidence")
    store.freeze("T-001", "evidence.log")
    result = store.remove("T-001", "evidence.log", ack_frozen=True)
    assert result.pin_removed and result.trashed_to is not None
    assert not path.exists()
    assert "documents" not in _card_yaml(store)  # last pin gone → key gone
    assert store.scan("T-001") == []


def test_rm_dangling_pin_heals_with_ack(store):
    path = _card_dir(store) / "evidence.log"
    path.write_bytes(b"evidence")
    store.freeze("T-001", "evidence.log")
    path.unlink()
    result = store.remove("T-001", "evidence.log", ack_frozen=True)
    assert result.pin_removed and result.trashed_to is None
    assert store.scan("T-001") == []


def test_rm_unknown_document_raises(store):
    with pytest.raises(UnknownDocumentError):
        store.remove("T-001", "ghost.md")


def test_rm_nested_document_preserves_subpath_in_trash(store):
    nested = _card_dir(store) / "attachments"
    nested.mkdir()
    (nested / "old.md").write_text("legacy")
    result = store.remove("T-001", "attachments/old.md")
    assert result.trashed_to is not None
    assert result.trashed_to.as_posix().endswith("T-001/attachments/old.md")


# ----- name validation --------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "/etc/passwd", "../escape.md", ".hidden", "task.yaml"])
def test_invalid_names_are_typed_errors(store, bad):
    with pytest.raises(DocumentNameError):
        store.freeze("T-001", bad)
    with pytest.raises(DocumentNameError):
        store.remove("T-001", bad)


# ----- legacy compatibility ----------------------------------------------------


def test_freeze_preserves_legacy_attachments_field_verbatim(store, tasks_dir):
    # Old cards carry an `attachments` manifest — K2 never touches it.
    card_path = _card_dir(store) / "task.yaml"
    raw = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    legacy = [{"name": "old.md", "digest": "abc123def456", "added": "2026-01-01T00:00:00Z"}]
    raw["attachments"] = legacy
    card_path.write_text(yaml.dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (_card_dir(store) / "new.log").write_bytes(b"new")
    store.freeze("T-001", "new.log")
    saved = _card_yaml(store)
    assert saved["attachments"] == legacy  # round-trip verbatim via extras
    assert saved["documents"][0]["name"] == "new.log"


def test_old_card_with_attachments_scans_fine(store):
    # The manifest is data, not the ledger: only real files are the view.
    card_path = _card_dir(store) / "task.yaml"
    raw = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    raw["attachments"] = [{"name": "phantom.md", "digest": "abc123def456"}]
    card_path.write_text(yaml.dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    assert store.scan("T-001") == []
