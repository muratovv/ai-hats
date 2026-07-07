"""Unit tests for ``ai_hats_tracker.attachments`` domain logic (HATS-402)."""

from __future__ import annotations

import subprocess
from pathlib import Path


from ai_hats_tracker.attachments import (
    DIGEST_LEN,
    DivergenceKind,
    FileOp,
    ReconcileAction,
    attachments_dir,
    compute_digest,
    is_binary,
    is_git_tracked,
    reconcile,
    verify_manifest,
)
from ai_hats.models import Attachment, TaskCard


def _make_card(*, attachments: list[Attachment] | None = None) -> TaskCard:
    return TaskCard(id="HATS-T1", title="t", attachments=attachments or [])


def _card_dir(tmp_path: Path) -> Path:
    """Create and return tmp_path/HATS-T1/ — the task card directory."""
    d = tmp_path / "HATS-T1"
    d.mkdir()
    return d


# ---------- compute_digest ----------


def test_compute_digest_known_value(tmp_path):
    f = tmp_path / "x.txt"
    f.write_bytes(b"hello")
    # sha256("hello") = 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
    assert compute_digest(f) == "2cf24dba5fb0"
    assert len(compute_digest(f)) == DIGEST_LEN


def test_compute_digest_distinguishes_content(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    assert compute_digest(a) != compute_digest(b)


# ---------- attachments_dir / is_binary ----------


def test_attachments_dir():
    assert attachments_dir(Path("/x/HATS-1")) == Path("/x/HATS-1/attachments")


def test_is_binary_text_file(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("plain text\nline two\n")
    assert not is_binary(f)


def test_is_binary_with_nul(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc\x00def")
    assert is_binary(f)


# ---------- is_git_tracked ----------


def test_is_git_tracked_outside_repo(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    # tmp_path is not a git repo
    assert not is_git_tracked(f)


def test_is_git_tracked_committed_file(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    f = tmp_path / "tracked.txt"
    f.write_text("x")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    assert is_git_tracked(f)


def test_is_git_tracked_staged_new_file_is_tracked(tmp_path):
    """Staged-new files are tracked from git's POV (they live in the index).

    Semantically that's the right answer for ``attach remove`` policy: ``git
    restore --staged`` can still recover a staged-new file's content.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    f = tmp_path / "staged.txt"
    f.write_text("x")
    subprocess.run(["git", "add", "staged.txt"], cwd=tmp_path, check=True)
    assert is_git_tracked(f)


# ---------- reconcile: ADDED ----------


def test_reconcile_added_for_blob_outside_attachments(tmp_path):
    card = _make_card()
    card_dir = _card_dir(tmp_path)
    blob = tmp_path / "incoming.md"
    blob.write_text("hello")

    r = reconcile(card, card_dir, blob, name="incoming.md")

    assert r.action is ReconcileAction.ADDED
    assert r.file_op is FileOp.MOVE
    assert r.attachment is not None
    assert r.attachment.name == "incoming.md"
    assert r.attachment.digest == compute_digest(blob)


# ---------- reconcile: REGISTERED_EXISTING (HATS-213 case) ----------


def test_reconcile_registered_existing_for_blob_already_inside(tmp_path):
    card = _make_card()
    card_dir = _card_dir(tmp_path)
    attachments_dir(card_dir).mkdir()
    blob = attachments_dir(card_dir) / "legacy.md"
    blob.write_text("legacy content")

    r = reconcile(card, card_dir, blob, name="legacy.md")

    assert r.action is ReconcileAction.REGISTERED_EXISTING
    assert r.file_op is FileOp.NONE
    assert r.attachment is not None
    assert r.attachment.name == "legacy.md"


# ---------- reconcile: NOOP ----------


def test_reconcile_noop_when_digest_matches(tmp_path):
    card_dir = _card_dir(tmp_path)
    blob = tmp_path / "x.md"
    blob.write_text("same")
    existing = Attachment(
        name="x.md", digest=compute_digest(blob), added="2026-05-20T00:00:00Z"
    )
    card = _make_card(attachments=[existing])

    r = reconcile(card, card_dir, blob, name="x.md")

    assert r.action is ReconcileAction.NOOP
    assert r.file_op is FileOp.NONE
    assert "matches" in r.message


# ---------- reconcile: ERROR_COLLISION ----------


def test_reconcile_error_collision_on_different_content_same_name(tmp_path):
    card_dir = _card_dir(tmp_path)
    blob = tmp_path / "x.md"
    blob.write_text("new content")
    existing = Attachment(
        name="x.md",
        digest="aaaaaaaaaaaa",  # arbitrary 12-hex, different from new content
        added="2026-05-20T00:00:00Z",
    )
    card = _make_card(attachments=[existing])

    r = reconcile(card, card_dir, blob, name="x.md")

    assert r.action is ReconcileAction.ERROR_COLLISION
    assert r.attachment is None
    assert r.existing_digest == "aaaaaaaaaaaa"
    assert r.new_digest == compute_digest(blob)
    assert "different content" in r.message


# ---------- verify_manifest ----------


def test_verify_manifest_clean(tmp_path):
    card_dir = _card_dir(tmp_path)
    attachments_dir(card_dir).mkdir()
    blob = attachments_dir(card_dir) / "ok.md"
    blob.write_text("ok")
    card = _make_card(
        attachments=[
            Attachment(name="ok.md", digest=compute_digest(blob), added="")
        ]
    )

    assert verify_manifest(card, card_dir) == []


def test_verify_manifest_blob_without_entry(tmp_path):
    card_dir = _card_dir(tmp_path)
    attachments_dir(card_dir).mkdir()
    (attachments_dir(card_dir) / "orphan.md").write_text("o")
    card = _make_card()

    divs = verify_manifest(card, card_dir)
    assert len(divs) == 1
    assert divs[0].kind is DivergenceKind.BLOB_WITHOUT_ENTRY
    assert divs[0].name == "orphan.md"


def test_verify_manifest_entry_without_blob(tmp_path):
    card_dir = _card_dir(tmp_path)
    attachments_dir(card_dir).mkdir()
    card = _make_card(
        attachments=[Attachment(name="ghost.md", digest="0123456789ab", added="")]
    )

    divs = verify_manifest(card, card_dir)
    assert len(divs) == 1
    assert divs[0].kind is DivergenceKind.ENTRY_WITHOUT_BLOB
    assert divs[0].name == "ghost.md"


def test_verify_manifest_digest_drift(tmp_path):
    card_dir = _card_dir(tmp_path)
    attachments_dir(card_dir).mkdir()
    blob = attachments_dir(card_dir) / "drift.md"
    blob.write_text("on disk")
    card = _make_card(
        attachments=[
            Attachment(name="drift.md", digest="ffffffffffff", added="")
        ]
    )

    divs = verify_manifest(card, card_dir)
    assert len(divs) == 1
    assert divs[0].kind is DivergenceKind.DIGEST_DRIFT
    assert divs[0].name == "drift.md"


def test_verify_manifest_missing_attachments_folder(tmp_path):
    """Card with no attachments and no folder on disk → clean."""
    card_dir = _card_dir(tmp_path)
    card = _make_card()
    assert verify_manifest(card, card_dir) == []
