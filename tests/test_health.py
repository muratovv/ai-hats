"""Layer triage (HATS-595, HATS-1163).

Fail-under-revert: drop the DATA rows from ``triage`` and
``test_data_layer_broken_when_tracker_missing`` goes red; drop the runtime-hooks
row and ``test_runtime_hooks_broken_when_manifest_entry_has_no_file`` goes red.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
import shutil
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_hats.health import Layer, Status, triage
from ai_hats.migration_backup import ENV_BACKUP_DIR, snapshot_pre_bump
from ai_hats.paths._dirs import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
from ai_hats.update_check import CacheEntry, write_cache


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A structurally healthy project: every layer path present."""
    ai_hats = tmp_path / ".agent" / "ai-hats"
    (ai_hats / "tracker" / "backlog").mkdir(parents=True)
    (ai_hats / "user-rules").mkdir()
    (ai_hats / "library" / "hooks").mkdir(parents=True)
    return tmp_path


def _row(reports, name):
    return next(r for r in reports if r.name == name)


def test_healthy_project_reports_no_broken_rows(project: Path) -> None:
    assert [r for r in triage(ProjectLayout.at(project)) if r.status is Status.BROKEN] == []


def test_data_layer_broken_when_tracker_missing(project: Path, tmp_path: Path) -> None:
    shutil.rmtree(project / ".agent" / "ai-hats" / "tracker")

    row = _row(triage(ProjectLayout.at(project)), "tracker")

    assert row.layer is Layer.DATA
    assert row.status is Status.BROKEN


def test_managed_layer_broken_when_library_missing(project: Path) -> None:
    shutil.rmtree(project / ".agent" / "ai-hats" / "library")

    row = _row(triage(ProjectLayout.at(project)), "library")

    assert row.layer is Layer.MANAGED
    assert row.status is Status.BROKEN
    assert "self init" in row.remediation


def test_no_imports_md_row_is_reported(project: Path) -> None:
    """HATS-1203: the aggregator is retired, so triage must not probe for it —
    a project without one is healthy, not BROKEN.
    """
    assert not [r for r in triage(ProjectLayout.at(project)) if r.name == "imports.md"]
    assert not (project / ".agent" / "ai-hats" / "imports.md").exists()


# ----- composed probes (S2) -----


def _write_settings(project: Path, command: str) -> None:
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": command}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def test_hook_refs_broken_row_names_the_dangling_command(project: Path) -> None:
    _write_settings(project, "$CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/gone.sh")

    row = _row(triage(ProjectLayout.at(project)), "hook refs")

    assert row.layer is Layer.MANAGED
    assert row.status is Status.BROKEN
    assert "gone.sh" in row.detail


def test_hook_refs_ok_when_every_command_resolves(project: Path) -> None:
    hook = project / ".agent" / "ai-hats" / "library" / "hooks" / "live.sh"
    hook.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    _write_settings(project, "$CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/live.sh")

    assert _row(triage(ProjectLayout.at(project)), "hook refs").status is Status.OK


def test_data_remediation_points_at_the_newest_snapshot(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_BACKUP_DIR, str(tmp_path / "backups"))
    newest = snapshot_pre_bump(project, label="newest")
    assert newest is not None
    slug = newest.name.split("-")[1]
    (newest.parent / f"20200101T000000Z-{slug}-older.tar.gz").write_bytes(b"")
    shutil.rmtree(project / ".agent" / "ai-hats" / "tracker")

    row = _row(triage(ProjectLayout.at(project)), "tracker")

    assert row.status is Status.BROKEN
    assert str(newest) in row.remediation
    assert "tar -xzf" in row.remediation


def test_data_remediation_degrades_when_no_snapshot_exists(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_BACKUP_DIR, str(tmp_path / "empty"))
    shutil.rmtree(project / ".agent" / "ai-hats" / "tracker")

    row = _row(triage(ProjectLayout.at(project)), "tracker")

    assert row.status is Status.BROKEN
    assert "no snapshot found" in row.remediation


def test_drift_warns_when_cache_says_behind(project: Path) -> None:
    _write_update_cache(project, behind=3, ahead=0)

    row = _row(triage(ProjectLayout.at(project)), "version drift")

    assert row.layer is Layer.RUNTIME
    assert row.status is Status.WARN
    assert "self update" in row.remediation


def test_drift_is_ok_when_cache_says_current(project: Path) -> None:
    _write_update_cache(project, behind=0, ahead=0)

    assert _row(triage(ProjectLayout.at(project)), "version drift").status is Status.OK


def test_drift_is_unknown_without_cache_and_never_broken(project: Path) -> None:
    row = _row(triage(ProjectLayout.at(project)), "version drift")

    assert row.status is Status.OK
    assert "unknown" in row.detail


def _write_update_cache(project: Path, *, behind: int, ahead: int) -> None:
    write_cache(
        ProjectLayout.at(project).cache,
        CacheEntry(
            checked_at=datetime.now(timezone.utc),
            installed_sha="a" * 40,
            latest_sha="b" * 40,
            remote_url="https://example.invalid/repo.git",
            behind=behind,
            ahead=ahead,
        ),
    )


def test_triage_warns_at_most_once_about_a_leaked_dir_pin(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One resolution of ai_hats_dir, so the HATS-897 notice cannot spam the table."""
    # HATS-897 warns only when both vars are set and the pin names another project.
    foreign = project.parent / "other-project"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(foreign / ".agent" / "ai-hats"))
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(foreign))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        triage(ProjectLayout.at(project))

    leaked = [w for w in caught if "pinned to project" in str(w.message)]
    assert len(leaked) <= 1, [str(w.message) for w in leaked]
