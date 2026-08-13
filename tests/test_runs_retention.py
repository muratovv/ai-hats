"""Retention for ``sessions/runs/`` (HATS-1339, S5).

The load-bearing property is not "old files go away" — it is that everything
this module has not been told is expendable **stays**. So the allowlist tests
here outnumber the expiry ones on purpose: an unknown filename, a facts-tier
file of any age, and the run dir itself all have to survive a sweep that is
doing its job.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from ai_hats.paths import session_cache_dir
from ai_hats.runs_retention import (
    BULK_MAX_AGE_DAYS,
    EXPIRABLE_ARTIFACTS,
    RETAINED_ARTIFACTS,
    STAMP_NAME,
    sweep_runs,
)
from ai_hats.session_liveness import write_session_anchor

DAY = 86400
OLD_SID = "session_20240101-120000-1-4242"
NEW_SID = "session_29991231-120000-1-4242"


def _runs(project_dir: Path) -> Path:
    root = project_dir / ".agent" / "ai-hats" / "sessions" / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _artifact(run_dir: Path, name: str, *, age_days: float, body: str = "x") -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / name
    path.write_text(body)
    when = time.time() - age_days * DAY
    os.utime(path, (when, when))
    return path


def _sweep(project_dir: Path, **kwargs):
    kwargs.setdefault("min_interval_hours", 0)
    return sweep_runs(project_dir, **kwargs)


def test_bulk_artifact_past_the_bound_is_dropped(tmp_path):
    run = _runs(tmp_path) / OLD_SID
    trace = _artifact(run, "trace.log", age_days=BULK_MAX_AGE_DAYS + 1)

    report = _sweep(tmp_path)

    assert not trace.exists()
    assert report.files_removed == 1


def test_bulk_artifact_within_the_bound_survives(tmp_path):
    run = _runs(tmp_path) / OLD_SID
    trace = _artifact(run, "trace.log", age_days=BULK_MAX_AGE_DAYS - 1)

    report = _sweep(tmp_path)

    assert trace.exists()
    assert report.files_removed == 0


@pytest.mark.parametrize("name", sorted(RETAINED_ARTIFACTS))
def test_every_facts_tier_file_survives_any_age(tmp_path, name):
    """The whole point of the tier: age is not a reason to drop these."""
    run = _runs(tmp_path) / OLD_SID
    kept = _artifact(run, name, age_days=BULK_MAX_AGE_DAYS * 100)
    _artifact(run, "trace.log", age_days=BULK_MAX_AGE_DAYS * 100)

    report = _sweep(tmp_path)

    assert kept.exists(), f"{name} is facts-tier and must never expire"
    assert report.files_removed == 1


def test_unknown_filename_survives(tmp_path):
    """Allowlist-by-default: a name in neither tier is not a deletion candidate.

    This is the regression guard for the failure mode that matters — a future
    artifact nobody added here must cost disk, never evidence.
    """
    run = _runs(tmp_path) / OLD_SID
    stranger = _artifact(run, "something-nobody-declared.bin", age_days=BULK_MAX_AGE_DAYS * 100)
    prompt = _artifact(run, "prompt-20240101T120000.txt", age_days=BULK_MAX_AGE_DAYS * 100)

    report = _sweep(tmp_path)

    assert stranger.exists()
    assert prompt.exists()
    assert report.files_removed == 0


def test_retained_names_are_never_expirable():
    """The subtraction at import, not a call-site check, is what enforces this."""
    assert not (EXPIRABLE_ARTIFACTS & RETAINED_ARTIFACTS)


def test_the_facts_tier_is_exactly_these_names():
    """Pinned by LITERAL, because the parametrized survival test above reads the
    same constant it is meant to guard — moving a name out of the tier keeps that
    test green while the file it named starts being deleted."""
    assert RETAINED_ARTIFACTS == frozenset(
        {"audit.md", "metrics.json", "retro.log", "diagnostics.json"}
    )


# ----- A live session's run is off limits at any age (HATS-1339 review) -----


@pytest.fixture
def _own_cache_home(tmp_path, monkeypatch):
    """Keep ``session_cache_dir`` inside the test's tmp tree, never the real one."""
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache_home"))


def _claim(project_dir: Path, run_name: str) -> Path:
    """Give the run's session a live owner — this very test process."""
    cache = session_cache_dir(project_dir, run_name[len("session_") :])
    cache.mkdir(parents=True, exist_ok=True)
    write_session_anchor(cache)
    return cache


def test_a_live_sessions_run_survives_however_far_past_the_bound(tmp_path, _own_cache_home):
    """The bound cannot protect a RUNNING session: ``meta_prompt.txt`` is written
    once at start, so its mtime is the session's own age and a long enough run
    crosses any bound while live. Only the owner check can answer this."""
    run = _runs(tmp_path) / OLD_SID
    prompt = _artifact(run, "meta_prompt.txt", age_days=BULK_MAX_AGE_DAYS * 10)
    _claim(tmp_path, OLD_SID)

    report = _sweep(tmp_path)

    assert prompt.exists(), "a live session lost the prompt it was launched with"
    assert report.files_removed == 0


def test_a_finished_sessions_run_is_still_swept(tmp_path, _own_cache_home):
    """The control: same file, same age, no cache dir. That is what finished
    looks like on disk — the session-cache sweep runs first and reaps it."""
    run = _runs(tmp_path) / OLD_SID
    prompt = _artifact(run, "meta_prompt.txt", age_days=BULK_MAX_AGE_DAYS * 10)

    report = _sweep(tmp_path)

    assert not prompt.exists()
    assert report.files_removed == 1


def test_report_carries_counts_and_bytes(tmp_path):
    run = _runs(tmp_path) / OLD_SID
    _artifact(run, "trace.log", age_days=BULK_MAX_AGE_DAYS + 1, body="a" * 100)
    _artifact(run, "transcript.jsonl", age_days=BULK_MAX_AGE_DAYS + 1, body="b" * 40)
    _artifact(run, "audit.md", age_days=BULK_MAX_AGE_DAYS + 1, body="c" * 999)

    report = _sweep(tmp_path)

    assert report.files_removed == 2
    assert report.bytes_removed == 140
    assert report.dirs_visited == 1
    assert report.errors == 0
    assert "2 files" in report.summary()
    assert "140 bytes" in report.summary()


def test_second_call_is_a_no_op(tmp_path):
    run = _runs(tmp_path) / OLD_SID
    _artifact(run, "trace.log", age_days=BULK_MAX_AGE_DAYS + 1)
    audit = _artifact(run, "audit.md", age_days=BULK_MAX_AGE_DAYS + 1)

    first = _sweep(tmp_path)
    second = _sweep(tmp_path)

    assert first.files_removed == 1
    assert second.files_removed == 0
    assert second.bytes_removed == 0
    assert audit.exists()


def test_run_dir_survives_even_when_every_file_in_it_expires(tmp_path):
    run = _runs(tmp_path) / OLD_SID
    for name in sorted(EXPIRABLE_ARTIFACTS):
        _artifact(run, name, age_days=BULK_MAX_AGE_DAYS + 1)

    report = _sweep(tmp_path)

    assert run.is_dir(), "a run dir must never disappear — the session id must still resolve"
    assert list(run.iterdir()) == []
    assert report.files_removed == len(EXPIRABLE_ARTIFACTS)


def test_a_run_started_within_the_bound_is_not_opened(tmp_path):
    """Id-derived start time floors file age — a stale mtime cannot fake it."""
    run = _runs(tmp_path) / NEW_SID
    trace = _artifact(run, "trace.log", age_days=BULK_MAX_AGE_DAYS * 100)

    report = _sweep(tmp_path)

    assert trace.exists()
    assert report.dirs_visited == 0


def test_non_session_entries_are_left_alone(tmp_path):
    root = _runs(tmp_path)
    pipeline = root / "pipeline_runs" / "reflect-session" / "20240101-120000-1"
    stray = _artifact(pipeline, "trace.log", age_days=BULK_MAX_AGE_DAYS + 1)

    report = _sweep(tmp_path)

    assert stray.exists(), "only session_* run dirs are in scope"
    assert report.dirs_visited == 0


def test_a_symlink_wearing_a_bulk_name_is_not_followed(tmp_path):
    """The one route by which a sweep could reach a protected record."""
    protected = tmp_path / "retro.md"
    protected.write_text("a hand-written record")
    run = _runs(tmp_path) / OLD_SID
    run.mkdir(parents=True)
    link = run / "trace.log"
    link.symlink_to(protected)
    when = time.time() - (BULK_MAX_AGE_DAYS + 1) * DAY
    os.utime(link, (when, when), follow_symlinks=False)

    report = _sweep(tmp_path)

    assert protected.exists()
    assert link.is_symlink()
    assert report.files_removed == 0


def test_missing_runs_dir_is_a_cheap_no_op(tmp_path):
    report = _sweep(tmp_path)

    assert report.files_removed == 0
    assert report.errors == 0
    assert not (tmp_path / ".agent").exists()


def test_a_recent_stamp_short_circuits_the_walk(tmp_path):
    run = _runs(tmp_path) / OLD_SID
    trace = _artifact(run, "trace.log", age_days=BULK_MAX_AGE_DAYS + 1)
    (_runs(tmp_path) / STAMP_NAME).touch()

    report = sweep_runs(tmp_path, min_interval_hours=6)

    assert report.skipped
    assert trace.exists()


def test_an_unreadable_stamp_falls_through_to_a_real_sweep(tmp_path):
    """Exercises the ``silent-ok`` branch in ``_swept_within``.

    A stamp that cannot be stat'd must not be read as "swept recently" — that
    would disable retention forever with no diagnostic.
    """
    root = _runs(tmp_path)
    run = root / OLD_SID
    trace = _artifact(run, "trace.log", age_days=BULK_MAX_AGE_DAYS + 1)
    (root / STAMP_NAME).symlink_to(tmp_path / "nowhere")

    report = sweep_runs(tmp_path, min_interval_hours=6)

    assert not report.skipped
    assert not trace.exists()


def test_an_undeletable_artifact_is_counted_and_logged(tmp_path, caplog):
    """No silent cap: a failed unlink reports rather than passing as clean."""
    run = _runs(tmp_path) / OLD_SID
    _artifact(run, "trace.log", age_days=BULK_MAX_AGE_DAYS + 1)
    os.chmod(run, 0o500)
    try:
        with caplog.at_level("WARNING"):
            report = _sweep(tmp_path)
    finally:
        os.chmod(run, 0o700)

    assert report.errors == 1
    assert report.files_removed == 0
    assert "cannot drop" in caplog.text
