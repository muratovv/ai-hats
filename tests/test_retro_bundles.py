"""Tests for BundleManager and next_bundle_id."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ai_hats.retro.bundle import BundleV1
from ai_hats.retro.bundles import BundleManager, next_bundle_id
from ai_hats.retro.loader import load


# --- fixtures ---


def _make_session(project: Path, session_id: str, *, productive: bool = True) -> None:
    """Create a fake .gitlog/session_<id>/ directory so existence checks pass."""
    sdir = project / ".gitlog" / f"session_{session_id}"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "audit.md").write_text(f"# Session Audit: {session_id}\n")
    if productive:
        (sdir / "metrics.json").write_text(
            '{"turns": 5, "tool_calls": 3, "exit_code": 0}'
        )


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """A fresh project tree with .gitlog/ ready for sessions."""
    (tmp_path / ".gitlog").mkdir()
    (tmp_path / ".agent" / "retrospectives" / "bundles").mkdir(parents=True)
    return tmp_path


# --- next_bundle_id ---


def test_next_bundle_id_empty_dir(tmp_path: Path) -> None:
    bundles_dir = tmp_path / "bundles"
    bundles_dir.mkdir()
    assert next_bundle_id(bundles_dir, today=date(2026, 4, 8)) == "BUNDLE-2026-04-08-001"


def test_next_bundle_id_increments_from_max(tmp_path: Path) -> None:
    bundles_dir = tmp_path / "bundles"
    bundles_dir.mkdir()
    for n in (1, 2, 3):
        (bundles_dir / f"BUNDLE-2026-04-08-{n:03d}.yaml").write_text("schema: hats-bundle/v1\n")
    assert next_bundle_id(bundles_dir, today=date(2026, 4, 8)) == "BUNDLE-2026-04-08-004"


def test_next_bundle_id_resets_across_days(tmp_path: Path) -> None:
    bundles_dir = tmp_path / "bundles"
    bundles_dir.mkdir()
    (bundles_dir / "BUNDLE-2026-04-07-005.yaml").write_text("schema: hats-bundle/v1\n")
    assert next_bundle_id(bundles_dir, today=date(2026, 4, 8)) == "BUNDLE-2026-04-08-001"


def test_next_bundle_id_ignores_malformed_filenames(tmp_path: Path) -> None:
    bundles_dir = tmp_path / "bundles"
    bundles_dir.mkdir()
    (bundles_dir / "not-a-bundle.yaml").write_text("noise")
    (bundles_dir / "BUNDLE-2026-04-08-001.txt").write_text("wrong ext")
    (bundles_dir / "BUNDLE-2026-04-08-001.yaml").write_text("schema: hats-bundle/v1\n")
    assert next_bundle_id(bundles_dir, today=date(2026, 4, 8)) == "BUNDLE-2026-04-08-002"


def test_next_bundle_id_missing_directory(tmp_path: Path) -> None:
    """Missing dir → BUNDLE-<today>-001 (do not crash)."""
    missing = tmp_path / "does-not-exist"
    assert next_bundle_id(missing, today=date(2026, 4, 8)) == "BUNDLE-2026-04-08-001"


# --- BundleManager.create ---


def test_bundle_manager_create_validates_session_ids_exist(project_dir: Path) -> None:
    bm = BundleManager(project_dir)
    with pytest.raises(ValueError, match="Session not found"):
        bm.create(["nonexistent-session"])


def test_bundle_manager_create_writes_yaml_and_roundtrips(project_dir: Path) -> None:
    _make_session(project_dir, "20260408-101010-1")
    _make_session(project_dir, "20260408-111111-1")
    bm = BundleManager(project_dir)
    bundle = bm.create(
        ["20260408-101010-1", "20260408-111111-1"],
        notes="test notes",
        now=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
    )
    assert bundle.bundle_id == "BUNDLE-2026-04-08-001"
    assert bundle.notes == "test notes"
    # roundtrip via loader
    loaded, _ = load(bm.path_of(bundle.bundle_id))
    assert isinstance(loaded, BundleV1)
    assert loaded.session_ids == ["20260408-101010-1", "20260408-111111-1"]
    assert loaded.notes == "test notes"


def test_bundle_manager_create_strips_session_prefix(project_dir: Path) -> None:
    """Inputs with `session_` prefix are normalized to bare ids."""
    _make_session(project_dir, "20260408-101010-1")
    bm = BundleManager(project_dir)
    bundle = bm.create(["session_20260408-101010-1"])
    assert bundle.session_ids == ["20260408-101010-1"]


def test_bundle_manager_list_returns_sorted(project_dir: Path) -> None:
    _make_session(project_dir, "20260408-101010-1")
    _make_session(project_dir, "20260408-202020-1")
    bm = BundleManager(project_dir)
    bm.create(["20260408-101010-1"], now=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc))
    bm.create(
        ["20260408-202020-1"],
        now=datetime(2026, 4, 8, 12, 1, tzinfo=timezone.utc),
    )
    bundles = bm.list()
    assert len(bundles) == 2
    assert bundles[0].bundle_id == "BUNDLE-2026-04-08-001"
    assert bundles[1].bundle_id == "BUNDLE-2026-04-08-002"


def test_bundle_manager_get_raises_on_missing(project_dir: Path) -> None:
    bm = BundleManager(project_dir)
    with pytest.raises(FileNotFoundError):
        bm.get("BUNDLE-2026-04-08-999")


def test_bundle_manager_create_idempotent_same_inputs(project_dir: Path) -> None:
    """Two creates with identical session sets → one bundle, returned twice."""
    _make_session(project_dir, "20260408-101010-1")
    _make_session(project_dir, "20260408-111111-1")
    bm = BundleManager(project_dir)

    first = bm.create(
        ["20260408-101010-1", "20260408-111111-1"],
        now=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
    )
    # different order, different timestamp, same effective inputs
    second = bm.create(
        ["20260408-111111-1", "20260408-101010-1"],
        now=datetime(2026, 4, 8, 12, 5, tzinfo=timezone.utc),
    )
    assert first.bundle_id == second.bundle_id
    # only one file on disk
    assert len(list((project_dir / ".agent" / "retrospectives" / "bundles").glob("BUNDLE-*.yaml"))) == 1


def test_bundle_manager_lens_agnostic_idempotency(project_dir: Path) -> None:
    """Bundles are lens-agnostic: same sessions → same bundle, regardless of how
    the user later judges them. Focus is no longer part of the bundle identity."""
    _make_session(project_dir, "20260408-101010-1")
    bm = BundleManager(project_dir)
    first = bm.create(["20260408-101010-1"])
    second = bm.create(["20260408-101010-1"])
    assert first.bundle_id == second.bundle_id


def test_bundle_manager_create_from_last(project_dir: Path) -> None:
    for sid in ("20260408-101010-1", "20260408-111111-1", "20260408-121212-1"):
        _make_session(project_dir, sid)
    bm = BundleManager(project_dir)
    bundle = bm.create_from_last(2)
    assert len(bundle.session_ids) == 2
    # SessionManager.list_sessions returns sorted; last 2 are the most recent
    assert bundle.session_ids == ["20260408-111111-1", "20260408-121212-1"]


def test_bundle_manager_create_from_since(project_dir: Path) -> None:
    _make_session(project_dir, "20260405-101010-1")
    _make_session(project_dir, "20260408-101010-1")
    bm = BundleManager(project_dir)
    bundle = bm.create_from_since(date(2026, 4, 8))
    assert bundle.session_ids == ["20260408-101010-1"]


def test_bundle_manager_create_empty_raises(project_dir: Path) -> None:
    bm = BundleManager(project_dir)
    with pytest.raises(ValueError, match="must not be empty"):
        bm.create([])


# --- CLI ---


def _setup_cli_project(tmp_path: Path, monkeypatch) -> Path:
    project = tmp_path / "p"
    project.mkdir()
    (project / "ai-hats.yaml").write_text("provider: claude\nschema_version: 1\n")
    (project / ".gitlog").mkdir()
    monkeypatch.chdir(project)
    return project


def test_bundle_cli_create_with_sessions(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from ai_hats.cli import main

    project = _setup_cli_project(tmp_path, monkeypatch)
    _make_session(project, "20260408-101010-1")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["bundle", "create", "--sessions", "20260408-101010-1", "--notes", "test bundle"],
    )
    assert result.exit_code == 0, result.output
    assert "Bundle" in result.output
    assert "BUNDLE-" in result.output
    bundles = list((project / ".agent" / "retrospectives" / "bundles").glob("BUNDLE-*.yaml"))
    assert len(bundles) == 1


def test_bundle_cli_rejects_focus_flag(tmp_path: Path, monkeypatch) -> None:
    """`bundle create --focus` was removed; flag should be unknown."""
    from click.testing import CliRunner

    from ai_hats.cli import main

    project = _setup_cli_project(tmp_path, monkeypatch)
    _make_session(project, "20260408-101010-1")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["bundle", "create", "--sessions", "20260408-101010-1", "--focus", "rejected"],
    )
    assert result.exit_code != 0
    assert "no such option" in result.output.lower() or "unrecognized" in result.output.lower()


def test_bundle_cli_create_requires_input(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from ai_hats.cli import main

    _setup_cli_project(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["bundle", "create"])
    assert result.exit_code == 1
    assert "--sessions" in result.output


def test_bundle_cli_list(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from ai_hats.cli import main

    project = _setup_cli_project(tmp_path, monkeypatch)
    _make_session(project, "20260408-101010-1")
    BundleManager(project).create(["20260408-101010-1"], notes="annotated")
    runner = CliRunner()
    result = runner.invoke(main, ["bundle", "list"])
    assert result.exit_code == 0, result.output
    assert "BUNDLE-" in result.output
    assert "annotated" in result.output


def test_bundle_cli_show(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from ai_hats.cli import main

    project = _setup_cli_project(tmp_path, monkeypatch)
    _make_session(project, "20260408-101010-1")
    b = BundleManager(project).create(["20260408-101010-1"], notes="hello")
    runner = CliRunner()
    result = runner.invoke(main, ["bundle", "show", b.bundle_id])
    assert result.exit_code == 0, result.output
    assert "hello" in result.output
    assert "20260408-101010-1" in result.output


def test_bundle_cli_show_missing(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from ai_hats.cli import main

    _setup_cli_project(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["bundle", "show", "BUNDLE-2026-04-08-999"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "Error" in result.output
