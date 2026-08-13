"""e2e (HATS-1339)

flow:   a developer starts a session in a project whose sessions/runs tree has
        been accumulating transcripts and traces for months
cmds:
    ai-hats -r maintainer
expect: bulk artifacts past the age bound are dropped and the drop is logged
        with counts and bytes, while every facts-tier file and every run dir stay
why:    runs/ grew 27 MB/day with no GC, and trimming audit.md would blind every
        retro that links to it
"""

# One real ``ai-hats`` run over a littered tree: HATS-1339 puts the sweep inside
# ``create_session``, so nothing short of a real session start reaches the seam
# that decides whether a project's evidence survives its disk bound.

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from ai_hats.runs_retention import BULK_MAX_AGE_DAYS, RETAINED_ARTIFACTS

from _helpers.fake_surface import install

pytestmark = pytest.mark.integration

#: The facts tier, named literally rather than imported: a rename that silently
#: dropped one of these from the retained set is exactly what this test exists to
#: catch, and importing the set under test would rename right along with it.
FACTS = ("audit.md", "metrics.json", "retro.log", "diagnostics.json")

#: Bulk artifacts old enough to expire. ``trace.log`` is deliberately absent —
#: it is the same tier, planted below with a fresh mtime.
STALE_BULK = (
    "transcript.jsonl",
    "meta_prompt.txt",
    "reasoning.log",
    "role_materialization.json",
    "usage.json",
    "transcript.txt",
    "pty_raw.log",
)

#: A name in neither set. The allowlist keeps it by construction (HATS-1280's
#: shape), so forgetting to catalogue a new artifact costs disk, never evidence.
UNCATALOGUED = "notes.md"

_AGED_RUN = "session_20250101-090000-1-4242"


def _runs_dir(project: Path) -> Path:
    return project / ".agent" / "ai-hats" / "sessions" / "runs"


def _write(path: Path, size: int, *, age_days: float) -> int:
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return size


def _recent_run_name() -> str:
    """A run id started yesterday — inside the bound however its files are aged."""
    started = datetime.now(timezone.utc) - timedelta(days=1)
    return f"session_{started.strftime('%Y%m%d-%H%M%S')}-1-4243"


@pytest.fixture
def littered(tmp_project, tmp_path: Path, repo_root: Path):
    """A runs tree with one long-expired run, one recent run, and a fresh file
    inside the expired one."""
    surface = install(tmp_project, tmp_path, repo_root)
    runs = _runs_dir(surface.project)
    old = runs / _AGED_RUN
    recent = runs / _recent_run_name()
    old.mkdir(parents=True)
    recent.mkdir(parents=True)

    stale_age = BULK_MAX_AGE_DAYS + 30
    expected_bytes = 0
    for index, name in enumerate(STALE_BULK):
        expected_bytes += _write(old / name, 100 + index, age_days=stale_age)
    for name in (*FACTS, UNCATALOGUED):
        _write(old / name, 64, age_days=stale_age)
    # Same dir, same tier, still being appended to — only its age spares it.
    _write(old / "trace.log", 55, age_days=0)
    _write(recent / "transcript.jsonl", 77, age_days=stale_age)
    _write(recent / "audit.md", 33, age_days=stale_age)

    yield surface, old, recent, expected_bytes


def test_the_next_run_expires_bulk_artifacts_and_keeps_every_fact(littered) -> None:
    """The bound applies to bulk files inside a run dir, never to the dir itself
    and never to the facts tier.

    ``audit.md`` is the hard one: ``retro.facts`` links every retro to
    ``../runs/<sid>/audit.md``, so expiring it by age would blind the reflect
    loop the way HATS-1374 did — silently, and only visible much later.
    """  # comment-length: allow — the retained tier's cost of being wrong
    surface, old, recent, expected_bytes = littered

    done = surface.run_once()

    survivors = sorted(p.name for p in old.iterdir())
    assert [name for name in STALE_BULK if name in survivors] == [], (
        f"bulk artifacts past the {BULK_MAX_AGE_DAYS}-day bound survived: {survivors}"
    )
    assert set(FACTS) <= set(survivors), (
        f"the facts tier lost a file — every retro links to audit.md: {survivors}"
    )
    assert set(FACTS) == set(RETAINED_ARTIFACTS), (
        "the retained set drifted from the tier this test pins"
    )
    assert UNCATALOGUED in survivors, (
        f"an unknown name was expired; the allowlist must keep it: {survivors}"
    )
    assert "trace.log" in survivors, (
        "a bulk artifact still being written was expired on its NAME rather than "
        "its age — a live session's own trace"
    )
    assert old.is_dir() and recent.is_dir(), "a run dir vanished; session ids must keep resolving"
    assert (recent / "transcript.jsonl").is_file(), (
        "a run that STARTED inside the bound was swept on its files' mtimes alone"
    )
    assert (
        f"runs retention: dropped {len(STALE_BULK)} files / {expected_bytes} bytes "
        f"across 1 run dirs (0 errors)" in done.stderr
    ), f"the sweep did not report counts and bytes; stderr:\n{done.stderr[-2000:]}"
