"""Unit tests for :mod:`ai_hats.safe_delete` — trash-bin destructive ops.

HATS-470: every test resets module state in autouse fixture, sets an
isolated trash base under ``tmp_path``, and asserts on either the
trash artefacts or the session summary.
"""
from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path

import pytest

from ai_hats import safe_delete
from ai_hats.safe_delete import (
    ENV_TRASH_DIR,
    HARD_DELETE_SENTINEL,
    TrashFullError,
    discard,
    replace,
    reset_session,
    session_root,
    session_summary,
)


# ---------------------- Fixtures ----------------------


@pytest.fixture(autouse=True)
def _clean_session(monkeypatch):
    """Fresh module state per test; don't inherit user env."""
    reset_session()
    monkeypatch.delenv(ENV_TRASH_DIR, raising=False)
    yield
    reset_session()


@pytest.fixture
def trash_base(tmp_path, monkeypatch):
    """Isolated trash base dir per test, env-pointed."""
    base = tmp_path / "trash-base"
    base.mkdir()
    monkeypatch.setenv(ENV_TRASH_DIR, str(base))
    return base


@pytest.fixture
def project(tmp_path):
    """Fake project root."""
    p = tmp_path / "project"
    p.mkdir()
    return p


# ---------------------- discard: basics ----------------------


def test_discard_missing_path_returns_none(project, trash_base):
    result = discard(project / "nonexistent.txt", project_dir=project)
    assert result is None
    # Lazy: no session created for a no-op.
    assert session_root() is None
    assert session_summary() is None


def test_discard_file_moves_to_trash_preserving_relpath(project, trash_base):
    f = project / "rules" / "foo.md"
    f.parent.mkdir(parents=True)
    f.write_text("user content")

    result = discard(f, reason="unit-test", project_dir=project)

    assert result is not None
    assert not f.exists()
    assert result.is_file()
    assert result.read_text() == "user content"
    assert result.relative_to(session_root()) == Path("rules/foo.md")


def test_discard_dir_moves_recursively(project, trash_base):
    d = project / "trait-dir"
    d.mkdir()
    (d / "inner.md").write_text("nested")
    (d / "sub").mkdir()
    (d / "sub" / "deep.md").write_text("deep")

    result = discard(d, project_dir=project)

    assert result is not None
    assert not d.exists()
    assert (result / "inner.md").read_text() == "nested"
    assert (result / "sub" / "deep.md").read_text() == "deep"


def test_discard_symlink_unlinks_link_preserves_target(
    project, trash_base, tmp_path
):
    target = tmp_path / "external_target.txt"
    target.write_text("external data")
    link = project / "alias"
    link.symlink_to(target)

    discard(link, project_dir=project)

    assert not link.exists()
    assert target.exists()
    assert target.read_text() == "external data"
    # Sidecar carries the original link target.
    sidecar = session_root() / "alias.symlink"
    assert sidecar.exists()
    assert sidecar.read_text() == str(target)


def test_discard_external_path_goes_under_external_subtree(
    project, trash_base, tmp_path
):
    external = tmp_path / "outside-project.txt"
    external.write_text("external")

    discard(external, project_dir=project)

    assert not external.exists()
    external_subtree = session_root() / "_external"
    assert external_subtree.is_dir()
    # File ended up somewhere under _external/ with its original tail.
    matches = list(external_subtree.rglob("outside-project.txt"))
    assert matches, f"expected outside-project.txt under {external_subtree}"


def test_discard_no_project_dir_goes_external(project, trash_base):
    f = project / "victim.txt"
    f.write_text("data")
    # Caller didn't pass project_dir at all.
    discard(f)
    assert not f.exists()
    assert (session_root() / "_external").is_dir()


# ---------------------- discard: tmp-artefact shortcut ----------------------


def test_discard_well_known_tmp_artefact_does_direct_cleanup(
    project, trash_base, monkeypatch
):
    """ai-hats-backup-* / ai-hats-trash-* under $TMPDIR are nuked directly.

    Note: ``tempfile.gettempdir()`` caches on first call, so we patch
    the module's reference directly rather than fiddling with env.
    """
    fake_tmp = trash_base.parent / "fake-tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(safe_delete.tempfile, "gettempdir", lambda: str(fake_tmp))

    victim = fake_tmp / "ai-hats-backup-20250101T000000Z"
    victim.mkdir()
    (victim / "inner.txt").write_text("legacy")

    discard(victim, reason="legacy-backup", project_dir=project)

    assert not victim.exists()
    # NOT moved into session_root (would just be трэш-в-трэш).
    assert not any(session_root().rglob("ai-hats-backup-*"))
    # But it WAS recorded in the manifest as clean-tmp.
    text = (session_root() / "MANIFEST.md").read_text()
    assert "clean-tmp" in text


def test_discard_under_current_trash_session_is_noop_not_recursion(
    project, trash_base
):
    """Path under the current trash root must NOT be re-trashed."""
    # First, prime a session via a real discard.
    f = project / "first.txt"
    f.write_text("first")
    discard(f, project_dir=project)

    # Now create a path under session_root and try to discard it.
    nested = session_root() / "_subdir"
    nested.mkdir(parents=True)
    nested_file = nested / "nested.txt"
    nested_file.write_text("nested")

    discard(nested_file, reason="recursion-test", project_dir=project)

    assert not nested_file.exists()
    # Did NOT spawn a new trash entry under session_root with that name.
    # (Direct cleanup happens because path is under session_root.)
    text = (session_root() / "MANIFEST.md").read_text()
    assert "clean-tmp" in text


def test_discard_arbitrary_tmp_file_still_moves_to_trash(
    project, trash_base, tmp_path
):
    """A random tmp-located file is NOT auto-direct-deleted — only
    paths matching the well-known ai-hats prefixes are."""
    victim = tmp_path / "random_tmp_garbage.txt"
    victim.write_text("garbage")

    discard(victim, project_dir=project)

    assert not victim.exists()
    # Lands under _external/ since it's outside project_dir.
    assert (session_root() / "_external").is_dir()
    assert any(session_root().rglob("random_tmp_garbage.txt"))


# ---------------------- discard: manifest ----------------------


def test_discard_records_manifest_with_reason(project, trash_base):
    f = project / "test.md"
    f.write_text("data")
    discard(f, reason="my-reason", project_dir=project)

    manifest = session_root() / "MANIFEST.md"
    assert manifest.exists()
    text = manifest.read_text()
    assert "discard" in text
    assert "my-reason" in text
    assert "test.md" in text
    assert "Recover:" in text


def test_discard_multiple_ops_append_to_manifest(project, trash_base):
    for name in ("a.md", "b.md", "c.md"):
        f = project / name
        f.write_text(name)
        discard(f, reason=f"r-{name}", project_dir=project)

    manifest = (session_root() / "MANIFEST.md").read_text()
    assert "r-a.md" in manifest
    assert "r-b.md" in manifest
    assert "r-c.md" in manifest


# ---------------------- replace: basics ----------------------


def test_replace_missing_file_just_writes(project, trash_base):
    f = project / "new.txt"
    result = replace(f, b"hello", reason="create", project_dir=project)

    assert result is False  # no snapshot taken
    assert f.read_bytes() == b"hello"
    assert session_root() is None  # no session for a fresh write


def test_replace_existing_snapshots_old_then_writes_new(project, trash_base):
    f = project / "config.yaml"
    f.write_bytes(b"old config")

    result = replace(f, b"new config", reason="update", project_dir=project)

    assert result is True
    assert f.read_bytes() == b"new config"
    snapshot = session_root() / "config.yaml"
    assert snapshot.is_file()
    assert snapshot.read_bytes() == b"old config"


def test_replace_bytes_identical_is_noop(project, trash_base):
    f = project / "same.txt"
    f.write_bytes(b"same")

    result = replace(f, b"same", project_dir=project)

    assert result is False
    assert session_root() is None  # no session created
    assert f.read_bytes() == b"same"


def test_replace_records_manifest_entry(project, trash_base):
    f = project / "doc.md"
    f.write_bytes(b"v1")
    replace(f, b"v2", reason="bump", project_dir=project)

    text = (session_root() / "MANIFEST.md").read_text()
    assert "replace" in text
    assert "bump" in text


# ---------------------- env: hard-delete sentinel ----------------------


def test_hard_delete_mode_discard_unlinks_without_trash(
    project, monkeypatch, capfd
):
    monkeypatch.setenv(ENV_TRASH_DIR, HARD_DELETE_SENTINEL)
    f = project / "doomed.txt"
    f.write_text("content")

    discard(f, reason="ci-run", project_dir=project)

    assert not f.exists()
    # Hard-delete mode reports None from session_root.
    assert session_root() is None
    err = capfd.readouterr().err
    assert "hard-deleted" in err
    assert "AI_HATS_TRASH_DIR=-" in err
    assert "ci-run" in err


def test_hard_delete_mode_replace_writes_without_snapshot(
    project, monkeypatch, capfd
):
    monkeypatch.setenv(ENV_TRASH_DIR, HARD_DELETE_SENTINEL)
    f = project / "doomed.txt"
    f.write_text("old")

    result = replace(f, b"new", reason="ci-update", project_dir=project)

    assert result is False
    assert f.read_bytes() == b"new"
    assert session_root() is None
    err = capfd.readouterr().err
    assert "hard-replaced" in err


def test_hard_delete_mode_summary_mentions_sentinel(project, monkeypatch):
    monkeypatch.setenv(ENV_TRASH_DIR, HARD_DELETE_SENTINEL)
    f = project / "doomed.txt"
    f.write_text("x")
    discard(f, project_dir=project)
    summary = session_summary()
    assert summary is not None
    assert "hard-delete" in summary
    assert "not recoverable" in summary


# ---------------------- env: default + custom base ----------------------


def test_default_base_under_tmpdir_ai_hats_namespace(project, monkeypatch):
    monkeypatch.delenv(ENV_TRASH_DIR, raising=False)
    f = project / "foo.txt"
    f.write_text("data")
    discard(f, project_dir=project)

    root = session_root()
    assert root is not None
    # $TMPDIR/ai-hats/trash-<ts>-<pid>/
    assert root.parent.name == "ai-hats"
    assert root.parent.parent == Path(tempfile.gettempdir())
    assert root.name.startswith("trash-")
    assert str(os.getpid()) in root.name


def test_custom_base_dir_via_env(project, tmp_path, monkeypatch):
    custom = tmp_path / "my-trash"
    custom.mkdir()
    monkeypatch.setenv(ENV_TRASH_DIR, str(custom))

    f = project / "foo.txt"
    f.write_text("data")
    discard(f, project_dir=project)

    root = session_root()
    assert root is not None
    assert root.parent == custom


# ---------------------- session lifecycle ----------------------


def test_session_lazy_not_created_for_missing_path(project, trash_base):
    discard(project / "ghost.txt", project_dir=project)
    assert session_root() is None
    assert session_summary() is None


def test_session_summary_after_multiple_ops(project, trash_base):
    (project / "a.txt").write_text("a")
    (project / "b.txt").write_text("b")
    discard(project / "a.txt", project_dir=project)
    discard(project / "b.txt", project_dir=project)

    summary = session_summary()
    assert summary is not None
    assert "2 op(s)" in summary
    assert str(session_root()) in summary


def test_reset_session_clears_state(project, trash_base):
    (project / "x.txt").write_text("x")
    discard(project / "x.txt", project_dir=project)
    assert session_root() is not None

    reset_session()
    assert session_root() is None
    assert session_summary() is None


# ---------------------- ENOSPC / TrashFullError ----------------------


def test_trash_full_on_session_create_raises_trash_full(
    project, tmp_path, monkeypatch
):
    """ENOSPC on mkdir → TrashFullError with actionable hint."""
    def fake_mkdir(self, *args, **kwargs):
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    f = project / "victim.txt"
    # write_text would also be intercepted by patched mkdir on parent;
    # set up project beforehand. project_dir already exists from fixture.
    # Manually drop the patch context to write the file.
    monkeypatch.undo()
    f.write_text("data")
    # Re-apply patch for the discard call.
    monkeypatch.setenv(ENV_TRASH_DIR, str(tmp_path / "fresh-base"))
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    with pytest.raises(TrashFullError) as exc:
        discard(f, project_dir=project)
    assert "no space" in str(exc.value).lower() or "ENOSPC" in str(exc.value)


def test_trash_full_on_move_raises_trash_full(project, trash_base, monkeypatch):
    """ENOSPC on shutil.move → TrashFullError."""
    f = project / "victim.txt"
    f.write_text("data")

    def boom(src, dst):
        raise OSError(errno.ENOSPC, "fake no space")

    monkeypatch.setattr(safe_delete.shutil, "move", boom)

    with pytest.raises(TrashFullError):
        discard(f, project_dir=project)


def test_read_only_fs_on_session_create_raises_trash_full(
    project, monkeypatch
):
    """EROFS on session create → TrashFullError with explicit message."""
    def fake_mkdir(self, *args, **kwargs):
        raise OSError(errno.EROFS, "read-only file system")

    monkeypatch.setenv(ENV_TRASH_DIR, "/some/read-only/path")
    f = project / "victim.txt"
    f.write_text("data")
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    with pytest.raises(TrashFullError) as exc:
        discard(f, project_dir=project)
    msg = str(exc.value).lower()
    assert "read-only" in msg or "permission" in msg


# ---------------------- regression / edge cases ----------------------


def test_discard_is_idempotent_after_missing(project, trash_base):
    """Calling discard twice on same missing path is a no-op both times."""
    assert discard(project / "ghost.txt", project_dir=project) is None
    assert discard(project / "ghost.txt", project_dir=project) is None


def test_discard_then_replace_share_one_session(project, trash_base):
    (project / "a.txt").write_text("a")
    (project / "b.txt").write_text("old")
    discard(project / "a.txt", project_dir=project)
    root_after_discard = session_root()
    replace(project / "b.txt", b"new", project_dir=project)
    assert session_root() == root_after_discard
    manifest = (session_root() / "MANIFEST.md").read_text()
    assert "discard" in manifest
    assert "replace" in manifest


def test_replace_does_not_create_session_on_pure_no_op(project, trash_base):
    """Bytes-identical replace must not leave an empty session dir."""
    f = project / "same.bin"
    f.write_bytes(b"identical")
    replace(f, b"identical", project_dir=project)
    assert session_root() is None
