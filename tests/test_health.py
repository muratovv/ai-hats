"""Layer triage (HATS-595, HATS-1163).

Fail-under-revert: drop the DATA rows from ``triage`` and
``test_data_layer_broken_when_tracker_missing`` goes red; drop the wt-hooks row
and ``test_wt_hooks_broken_when_manifest_entry_has_no_file`` goes red.
"""

from __future__ import annotations

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
    (ai_hats / "imports.md").write_text("", encoding="utf-8")
    return tmp_path


def _row(reports, name):
    return next(r for r in reports if r.name == name)


def test_healthy_project_reports_no_broken_rows(project: Path) -> None:
    assert [r for r in triage(project) if r.status is Status.BROKEN] == []


def test_data_layer_broken_when_tracker_missing(project: Path, tmp_path: Path) -> None:
    shutil.rmtree(project / ".agent" / "ai-hats" / "tracker")

    row = _row(triage(project), "tracker")

    assert row.layer is Layer.DATA
    assert row.status is Status.BROKEN


def test_managed_layer_broken_when_library_missing(project: Path) -> None:
    shutil.rmtree(project / ".agent" / "ai-hats" / "library")

    row = _row(triage(project), "library")

    assert row.layer is Layer.MANAGED
    assert row.status is Status.BROKEN
    assert "self init" in row.remediation


def test_managed_layer_broken_when_imports_md_missing(project: Path) -> None:
    (project / ".agent" / "ai-hats" / "imports.md").unlink()

    row = _row(triage(project), "imports.md")

    assert row.layer is Layer.MANAGED
    assert row.status is Status.BROKEN


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

    row = _row(triage(project), "hook refs")

    assert row.layer is Layer.MANAGED
    assert row.status is Status.BROKEN
    assert "gone.sh" in row.detail


def test_hook_refs_ok_when_every_command_resolves(project: Path) -> None:
    hook = project / ".agent" / "ai-hats" / "library" / "hooks" / "live.sh"
    hook.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    _write_settings(project, "$CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/live.sh")

    assert _row(triage(project), "hook refs").status is Status.OK


def test_data_remediation_points_at_the_newest_snapshot(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_BACKUP_DIR, str(tmp_path / "backups"))
    newest = snapshot_pre_bump(project, label="newest")
    assert newest is not None
    slug = newest.name.split("-")[1]
    (newest.parent / f"20200101T000000Z-{slug}-older.tar.gz").write_bytes(b"")
    shutil.rmtree(project / ".agent" / "ai-hats" / "tracker")

    row = _row(triage(project), "tracker")

    assert row.status is Status.BROKEN
    assert str(newest) in row.remediation
    assert "tar -xzf" in row.remediation


def test_data_remediation_degrades_when_no_snapshot_exists(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_BACKUP_DIR, str(tmp_path / "empty"))
    shutil.rmtree(project / ".agent" / "ai-hats" / "tracker")

    row = _row(triage(project), "tracker")

    assert row.status is Status.BROKEN
    assert "no snapshot found" in row.remediation


def test_drift_warns_when_cache_says_behind(project: Path) -> None:
    _write_update_cache(project, behind=3, ahead=0)

    row = _row(triage(project), "version drift")

    assert row.layer is Layer.RUNTIME
    assert row.status is Status.WARN
    assert "self update" in row.remediation


def test_drift_is_ok_when_cache_says_current(project: Path) -> None:
    _write_update_cache(project, behind=0, ahead=0)

    assert _row(triage(project), "version drift").status is Status.OK


def test_drift_is_unknown_without_cache_and_never_broken(project: Path) -> None:
    row = _row(triage(project), "version drift")

    assert row.status is Status.OK
    assert "unknown" in row.detail


def _write_update_cache(project: Path, *, behind: int, ahead: int) -> None:
    write_cache(
        project,
        CacheEntry(
            checked_at=datetime.now(timezone.utc),
            installed_sha="a" * 40,
            latest_sha="b" * 40,
            remote_url="https://example.invalid/repo.git",
            behind=behind,
            ahead=ahead,
        ),
    )


# ----- managed-dir completeness vs its own .manifest (HATS-1163) -----


def _seed_hook(project: Path, subdir: str, name: str, *, write_script: bool = True) -> Path:
    """Materialize a managed hook dir the way HooksManager does: manifest + script."""
    d = project / ".agent" / "ai-hats" / "library" / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / ".manifest").write_text(
        f"# ai-hats managed — do not edit\n{name}\n", encoding="utf-8"
    )
    script = d / name
    if write_script:
        script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    return script


def test_wt_hooks_broken_when_manifest_entry_has_no_file(project: Path) -> None:
    """The HATS-595 incident: manifest claims a script the merge needs, file is gone."""
    _seed_hook(project, "wt-hooks", "hunk-review-comments-drain-review.sh", write_script=False)

    row = _row(triage(project), "library/wt-hooks")

    assert row.layer is Layer.MANAGED
    assert row.status is Status.BROKEN
    assert "hunk-review-comments-drain-review.sh" in row.detail
    assert "self init" in row.remediation


def test_wt_hooks_ok_when_manifest_is_satisfied(project: Path) -> None:
    _seed_hook(project, "wt-hooks", "hunk-review-comments-drain-review.sh")

    assert _row(triage(project), "library/wt-hooks").status is Status.OK


def test_wt_hooks_ok_when_nothing_is_declared(project: Path) -> None:
    """No manifest and no dir is the healthy 'no worktree hooks declared' state.

    ``materialize_worktree_hooks`` returns early without creating either, so
    treating an absent wt-hooks dir as broken would fire on every project that
    composes no wt_out hook.
    """
    assert not (project / ".agent" / "ai-hats" / "library" / "wt-hooks").exists()

    assert _row(triage(project), "library/wt-hooks").status is Status.OK


def test_runtime_hooks_broken_when_manifest_entry_has_no_file(project: Path) -> None:
    _seed_hook(project, "hooks", "safety-guard-safety_gate.py", write_script=False)

    row = _row(triage(project), "library/hooks")

    assert row.status is Status.BROKEN
    assert "safety-guard-safety_gate.py" in row.detail


def test_hook_dirs_ok_without_a_manifest(project: Path) -> None:
    """An unmanifested dir declares nothing — presence alone stays the verdict."""
    assert _row(triage(project), "library/hooks").status is Status.OK


def test_manifest_check_tolerates_the_hashed_marker_format(project: Path) -> None:
    """HATS-911 hashed manifests are read by the shared reader, not parsed here."""
    d = project / ".agent" / "ai-hats" / "library" / "wt-hooks"
    d.mkdir(parents=True)
    (d / ".manifest").write_text(
        "# ai-hats-owner: hooks\nkept-hook.sh  deadbeef\n", encoding="utf-8"
    )

    row = _row(triage(project), "library/wt-hooks")

    assert row.status is Status.BROKEN
    assert "kept-hook.sh" in row.detail


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
        triage(project)

    leaked = [w for w in caught if "pinned to project" in str(w.message)]
    assert len(leaked) <= 1, [str(w.message) for w in leaked]
