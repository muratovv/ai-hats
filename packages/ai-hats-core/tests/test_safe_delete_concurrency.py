"""Concurrency test for :func:`ai_hats_core.safe_delete.discard` (HATS-941).

``discard`` is documented idempotent ("missing path → None") but that held only
SERIALLY: its ``exists()`` guard and ``_move_to_trash``'s ``shutil.move`` are
separated by a window, so two processes discarding the SAME path both pass the
guard and the loser raised ``FileNotFoundError`` (it bit the tracker's
legacy-backlog cleanup under concurrent sessions). N×R barrier-synced discards
of the same file must raise zero times, trashing each file once; revert the
source-vanished swallow and the loser's ``FileNotFoundError`` returns → RED.
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from ai_hats_core.safe_delete import ENV_TRASH_DIR


pytestmark = pytest.mark.integration

_N = 6
_ROUNDS = 25


def _discard_round_worker(files: list[str], results: dict, key: str, barrier) -> None:
    """Discard each file in lockstep with peers; record raises + won-moves.

    Top-level for multiprocessing (spawn) pickling. A raise is CAUGHT and
    recorded (not re-raised) so the worker stays in barrier lockstep — the
    parent asserts the recorded errors are empty.
    """
    from ai_hats_core.safe_delete import discard

    errors: list[str] = []
    dests = 0
    for f in files:
        try:
            barrier.wait(timeout=20)
        except Exception:  # noqa: BLE001 — a broken barrier ends the worker
            break
        try:
            dest = discard(Path(f), project_dir=Path(f).parent)
            if dest is not None:
                dests += 1
        except Exception as exc:  # noqa: BLE001 — recorded, asserted in parent
            errors.append(f"{type(exc).__name__}: {exc}")
    results[key] = {"errors": errors, "dests": dests}


def test_concurrent_discard_same_file_is_idempotent(tmp_path, monkeypatch) -> None:
    """N×R barrier-synced discards of the same file → 0 raises, trashed once each."""
    base = tmp_path / "trash-base"
    base.mkdir()
    monkeypatch.setenv(ENV_TRASH_DIR, str(base))  # spawn children inherit os.environ

    project = tmp_path / "project"
    project.mkdir()
    files = []
    for i in range(_ROUNDS):
        f = project / f"legacy-{i}.md"
        f.write_text(f"legacy {i}\n")
        files.append(str(f))

    manager = multiprocessing.Manager()
    results = manager.dict()
    barrier = manager.Barrier(_N)

    procs = [
        multiprocessing.Process(
            target=_discard_round_worker, args=(files, results, f"w{k}", barrier)
        )
        for k in range(_N)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=90)
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]

    outcomes = [dict(results[f"w{k}"]) for k in range(_N)]
    all_errors = [e for o in outcomes for e in o["errors"]]
    assert all_errors == [], f"concurrent discard raised (expected clean no-op): {all_errors[:5]}"

    # Each file was trashed exactly once across all workers (one winner/round).
    total_dests = sum(o["dests"] for o in outcomes)
    assert total_dests == _ROUNDS, f"expected {_ROUNDS} moves (one per file), got {total_dests}"

    for f in files:
        assert not Path(f).exists(), f"victim still present: {f}"
