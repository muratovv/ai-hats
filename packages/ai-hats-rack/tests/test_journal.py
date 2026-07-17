"""K7 audit journal: lossless persistence, rotation, identity, loud-fail
(HATS-1025; PROP-004/005/076/080)."""

from __future__ import annotations

import json

import pytest

from ai_hats_rack.dispatch import AbortOperation, Delta, DispatchRecord, OperationAborted
from ai_hats_rack.events import PreDestroyEvent
from ai_hats_rack.journal import (
    ACTIVE_NAME,
    ENV_ROOT_PID,
    ENV_SESSION_ID,
    JsonlJournalSink,
    journal_files,
    read_journal,
)
from ai_hats_rack.models import TaskCard
from rack_testkit import StubSubscriber, in_lock, make_kernel, post_lock


@pytest.fixture(autouse=True)
def _clean_identity_env(monkeypatch):
    """Deterministic identity baseline; tests opt in to env explicitly."""
    monkeypatch.delenv(ENV_SESSION_ID, raising=False)
    monkeypatch.delenv(ENV_ROOT_PID, raising=False)


def journaled_kernel(tasks_dir, *, max_bytes=1 << 20, **kwargs):
    sink = JsonlJournalSink(tasks_dir, max_bytes=max_bytes)
    return make_kernel(tasks_dir, journal_sink=sink, **kwargs)


def create(kernel, cwd, **kwargs) -> str:
    return kernel.create(actor="session:s1", caller_cwd=cwd, title="t", **kwargs).task.id


def synthetic_record(reason: str) -> DispatchRecord:
    return DispatchRecord(
        event_key="edge:a--b",
        task_id="T-1",
        actor="session:s1",
        force=False,
        reason=reason,
        result="persisted",
    )


# ----- persistence of every dispatch ----------------------------------------


def test_transition_journals_every_subscriber_outcome(tasks_dir, cwd):
    subs = [
        StubSubscriber("gate", [in_lock("edge:brainstorm--plan", priority=10)]),
        StubSubscriber(
            "wt",
            [in_lock("edge:brainstorm--plan", priority=20)],
            action=lambda ctx: Delta(work_log=("note",)),
        ),
        StubSubscriber("epic", [post_lock("edge:brainstorm--plan")]),
    ]
    kernel = journaled_kernel(tasks_dir, subscribers=subs)
    task_id = create(kernel, cwd)

    kernel.transition(task_id, "plan", actor="session:s1", caller_cwd=cwd)

    records, corrupt = read_journal(tasks_dir, task_id)
    assert corrupt == []
    assert len(records) == 1
    record = records[0]
    assert record["v"] == 1
    assert record["ts"]
    assert record["event"] == "edge:brainstorm--plan"
    assert record["task_id"] == task_id
    assert record["detail"] == {"from": "brainstorm", "to": "plan"}
    assert record["actor"] == "session:s1"
    assert record["force"] is False
    assert record["result"] == "persisted"
    assert [(o["subscriber"], o["phase"], o["outcome"]) for o in record["outcomes"]] == [
        ("gate", "in-lock", "ok"),
        ("wt", "in-lock", "delta"),
        ("epic", "post-lock", "ok"),
    ]
    assert record["outcomes"][1]["delta"] == {"work_log": ["note"]}


def test_abort_is_journaled_and_card_untouched(tasks_dir, cwd):
    gate = StubSubscriber(
        "gate",
        [in_lock("edge:brainstorm--plan")],
        action=lambda ctx: (_ for _ in ()).throw(AbortOperation("fill the plan")),
    )
    kernel = journaled_kernel(tasks_dir, subscribers=[gate])
    task_id = create(kernel, cwd)

    with pytest.raises(OperationAborted):
        kernel.transition(task_id, "plan", actor="session:s1", caller_cwd=cwd)

    assert TaskCard.from_yaml(tasks_dir / task_id / "task.yaml").state == "brainstorm"
    records, _ = read_journal(tasks_dir, task_id)
    assert len(records) == 1
    assert records[0]["result"] == "aborted"
    assert records[0]["outcomes"] == [
        {"subscriber": "gate", "phase": "in-lock", "outcome": "abort", "reason": "fill the plan"}
    ]


def test_lossless_long_reasons_round_trip_byte_for_byte(tasks_dir, cwd):
    # PROP-004: no truncation anywhere — 100K+ multibyte chars survive intact.
    force_reason = "π≥шляпа-forced/" + "x" * 100_000
    abort_reason = "отказ≠" + "y" * 100_000
    gate = StubSubscriber(
        "gate",
        [in_lock("edge:plan--execute")],
        action=lambda ctx: (_ for _ in ()).throw(AbortOperation(abort_reason)),
    )
    kernel = journaled_kernel(tasks_dir, subscribers=[gate])
    task_id = create(kernel, cwd)

    kernel.transition(
        task_id, "plan", actor="session:s1", caller_cwd=cwd, force=True, reason=force_reason
    )
    with pytest.raises(OperationAborted):
        kernel.transition(task_id, "execute", actor="session:s1", caller_cwd=cwd)

    records, _ = read_journal(tasks_dir, task_id)
    assert records[0]["reason"] == force_reason
    assert records[1]["outcomes"][0]["reason"] == abort_reason


def test_epicify_and_pre_destroy_records_carry_detail(tasks_dir, cwd):
    kernel = journaled_kernel(tasks_dir)
    parent = create(kernel, cwd)
    child = create(kernel, cwd, parent_task=parent)

    records, _ = read_journal(tasks_dir, parent)
    assert [(r["event"], r["detail"]) for r in records] == [
        ("epicify", {"epic": parent, "child": child})
    ]
    assert records[0]["result"] == "persisted"

    kernel.publish(PreDestroyEvent("worktree-merge", parent), actor="session:s1", caller_cwd=cwd)
    records, _ = read_journal(tasks_dir, parent)
    assert records[-1]["event"] == "pre-destroy"
    assert records[-1]["detail"] == {"operation": "worktree-merge"}


def test_aborted_pre_destroy_is_journaled(tasks_dir, cwd):
    guard = StubSubscriber(
        "guard",
        [in_lock("pre-destroy")],
        action=lambda ctx: (_ for _ in ()).throw(AbortOperation("notes not drained")),
    )
    kernel = journaled_kernel(tasks_dir, subscribers=[guard])
    task_id = create(kernel, cwd)

    with pytest.raises(OperationAborted):
        kernel.publish(
            PreDestroyEvent("worktree-merge", task_id), actor="session:s1", caller_cwd=cwd
        )

    records, _ = read_journal(tasks_dir, task_id)
    assert records[-1]["result"] == "aborted"
    assert records[-1]["outcomes"][0]["reason"] == "notes not drained"


# ----- rotation ---------------------------------------------------------------


def test_rotation_preserves_every_record_in_order(tasks_dir):
    sink = JsonlJournalSink(tasks_dir, max_bytes=400)
    for i in range(40):
        sink.record(synthetic_record(f"r{i:04d}"))

    files = journal_files(tasks_dir, "T-1")
    assert len(files) > 1  # rolled over at least once
    assert files[-1].name == ACTIVE_NAME
    assert all(f.name != ACTIVE_NAME for f in files[:-1])

    records, corrupt = read_journal(tasks_dir, "T-1")
    assert corrupt == []
    assert [r["reason"] for r in records] == [f"r{i:04d}" for i in range(40)]


def test_rotation_never_splits_a_record(tasks_dir):
    # A single record far above max_bytes lands whole in one file.
    sink = JsonlJournalSink(tasks_dir, max_bytes=100)
    big = synthetic_record("z" * 10_000)
    sink.record(big)
    sink.record(synthetic_record("after"))

    records, corrupt = read_journal(tasks_dir, "T-1")
    assert corrupt == []
    assert [len(r["reason"]) for r in records] == [10_000, 5]


# ----- loud but non-fatal write failures ---------------------------------------


def test_write_failure_is_loud_but_transaction_survives(tasks_dir, cwd, capsys):
    kernel = journaled_kernel(tasks_dir)
    task_id = create(kernel, cwd)
    (tasks_dir / task_id / ACTIVE_NAME).mkdir()  # journal path unwritable

    result = kernel.transition(task_id, "plan", actor="session:s1", caller_cwd=cwd)

    assert result.task.state == "plan"
    assert TaskCard.from_yaml(tasks_dir / task_id / "task.yaml").state == "plan"
    err = capsys.readouterr().err
    assert "AUDIT JOURNAL WRITE FAILED" in err
    assert task_id in err


# ----- identity (PROP-080/076) --------------------------------------------------


def test_identity_verified_against_environment(tasks_dir, cwd, monkeypatch):
    monkeypatch.setenv(ENV_SESSION_ID, "s1")
    monkeypatch.setenv(ENV_ROOT_PID, "4242")
    kernel = journaled_kernel(tasks_dir)
    task_id = create(kernel, cwd)
    kernel.transition(task_id, "plan", actor="session:s1", caller_cwd=cwd)

    identity = read_journal(tasks_dir, task_id)[0][0]["identity"]
    assert identity == {"session_id": "s1", "root_pid": 4242, "verdict": "verified"}


def test_identity_mismatch_is_marked(tasks_dir, cwd, monkeypatch):
    monkeypatch.setenv(ENV_SESSION_ID, "s1")
    kernel = journaled_kernel(tasks_dir)
    task_id = create(kernel, cwd)
    kernel.transition(task_id, "plan", actor="session:imposter", caller_cwd=cwd)

    identity = read_journal(tasks_dir, task_id)[0][0]["identity"]
    assert identity["verdict"] == "mismatch"
    assert "session:imposter" in identity["note"]
    assert "session:s1" in identity["note"]


def test_identity_without_env_is_an_explicit_blind_zone(tasks_dir, cwd):
    kernel = journaled_kernel(tasks_dir)
    task_id = create(kernel, cwd)
    kernel.transition(task_id, "plan", actor="human:someone", caller_cwd=cwd)

    identity = read_journal(tasks_dir, task_id)[0][0]["identity"]
    assert identity["verdict"] == "unverified"
    assert identity["session_id"] == ""
    assert identity["root_pid"] == 0


def test_ownership_holder_mismatch_is_marked(tasks_dir, cwd, monkeypatch):
    monkeypatch.setenv(ENV_SESSION_ID, "s1")
    kernel = journaled_kernel(tasks_dir)
    task_id = create(kernel, cwd)
    registry = {"owners": {task_id: {"session_id": "someone-else"}}, "version": 1}
    (tasks_dir.parent / "ownership.json").write_text(json.dumps(registry), encoding="utf-8")

    kernel.transition(task_id, "plan", actor="session:s1", caller_cwd=cwd)

    identity = read_journal(tasks_dir, task_id)[0][0]["identity"]
    assert identity["verdict"] == "verified"  # env check is independent
    assert identity["holder"] == "someone-else"
    assert identity["holder_mismatch"] is True


def test_ownership_holder_match_carries_no_mark(tasks_dir, cwd, monkeypatch):
    monkeypatch.setenv(ENV_SESSION_ID, "s1")
    kernel = journaled_kernel(tasks_dir)
    task_id = create(kernel, cwd)
    registry = {"owners": {task_id: {"session_id": "s1"}}, "version": 1}
    (tasks_dir.parent / "ownership.json").write_text(json.dumps(registry), encoding="utf-8")

    kernel.transition(task_id, "plan", actor="session:s1", caller_cwd=cwd)

    identity = read_journal(tasks_dir, task_id)[0][0]["identity"]
    assert identity["holder"] == "s1"
    assert "holder_mismatch" not in identity


# ----- reader resilience --------------------------------------------------------


def test_torn_line_is_reported_never_dropped(tasks_dir, cwd):
    kernel = journaled_kernel(tasks_dir)
    task_id = create(kernel, cwd)
    kernel.transition(task_id, "plan", actor="session:s1", caller_cwd=cwd)
    with (tasks_dir / task_id / ACTIVE_NAME).open("a", encoding="utf-8") as fh:
        fh.write('{"v": 1, "ts": "2026-')  # torn tail from a crashed writer

    records, corrupt = read_journal(tasks_dir, task_id)
    assert len(records) == 1  # the intact record still parses
    assert len(corrupt) == 1
    assert corrupt[0].raw == '{"v": 1, "ts": "2026-'
    assert corrupt[0].line_no == 2
