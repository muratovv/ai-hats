"""Materialization port: one seam for every session write (HATS-1211).

The port has two modes. ``apply`` performs the write and records it; ``plan``
records the intent and touches nothing. Report and reality are the same
statement run in two modes — see tasks/HATS-1211/plan.md §1.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats.materialization import MaterializationMode, Materializer, WriteKind


def test_write_text_apply_writes_and_records(tmp_path: Path):
    port = Materializer(MaterializationMode.APPLY)
    target = tmp_path / "session" / "prompt.md"

    port.write_text(target, "hello")

    assert target.read_text() == "hello"
    [entry] = port.plan.entries
    assert entry.kind is WriteKind.WRITE_TEXT
    assert entry.target == target
    assert entry.size == 5


def test_write_text_plan_records_intent_without_touching_disk(tmp_path: Path):
    port = Materializer(MaterializationMode.PLAN)
    target = tmp_path / "session" / "prompt.md"

    port.write_text(target, "hello")

    assert not target.exists()
    assert not target.parent.exists()  # not even the parent dir
    [entry] = port.plan.entries
    assert entry.kind is WriteKind.WRITE_TEXT
    assert entry.size == 5


def test_remove_tree_plan_leaves_the_tree_alone(tmp_path: Path):
    """The destructive shape: a dry-run must never delete a live session's dir."""
    doomed = tmp_path / "plugin"
    (doomed / "skills").mkdir(parents=True)
    (doomed / "skills" / "a.md").write_text("keep me")

    port = Materializer(MaterializationMode.PLAN)
    port.remove_tree(doomed)

    assert (doomed / "skills" / "a.md").read_text() == "keep me"
    [entry] = port.plan.entries
    assert entry.kind is WriteKind.REMOVE_TREE
    assert entry.target == doomed


def test_remove_tree_apply_removes(tmp_path: Path):
    doomed = tmp_path / "plugin"
    (doomed / "skills").mkdir(parents=True)

    port = Materializer(MaterializationMode.APPLY)
    port.remove_tree(doomed)

    assert not doomed.exists()


def test_remove_tree_missing_target_is_not_recorded(tmp_path: Path):
    """Callers guard with ``if dest.exists()``; a no-op must not enter the plan."""
    port = Materializer(MaterializationMode.APPLY)
    port.remove_tree(tmp_path / "never-existed")

    assert port.plan.entries == []


def _skill_src(root: Path) -> Path:
    src = root / "src-skill"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text("body")           # 4 bytes
    (src / "scripts" / "run.sh").write_text("echo")  # 4 bytes
    return src


def test_copy_tree_plan_sizes_the_copy_without_making_it(tmp_path: Path):
    src = _skill_src(tmp_path)
    dest = tmp_path / "cache" / "skills" / "s"

    port = Materializer(MaterializationMode.PLAN)
    port.copy_tree(src, dest)

    assert not dest.exists()
    [entry] = port.plan.entries
    assert entry.kind is WriteKind.COPY_TREE
    assert entry.target == dest
    assert entry.source == src
    assert entry.file_count == 2
    assert entry.size == 8


def test_copy_tree_apply_copies_and_reports_the_same_numbers(tmp_path: Path):
    src = _skill_src(tmp_path)
    dest = tmp_path / "cache" / "skills" / "s"

    port = Materializer(MaterializationMode.APPLY)
    port.copy_tree(src, dest)

    assert (dest / "SKILL.md").read_text() == "body"
    assert (dest / "scripts" / "run.sh").read_text() == "echo"
    [entry] = port.plan.entries
    assert entry.file_count == 2
    assert entry.size == 8


def test_mkdir_plan_creates_nothing(tmp_path: Path):
    target = tmp_path / "cache" / "sessions" / "sid"

    port = Materializer(MaterializationMode.PLAN)
    port.mkdir(target)

    assert not target.exists()
    assert not (tmp_path / "cache").exists()
    assert [e.kind for e in port.plan.entries] == [WriteKind.MKDIR]


def test_mkdir_existing_dir_is_not_recorded(tmp_path: Path):
    """Every category handler mkdirs the cache dir; only a real creation is news."""
    port = Materializer(MaterializationMode.APPLY)
    port.mkdir(tmp_path)

    assert port.plan.entries == []


def test_merge_json_plan_reports_the_diff_against_the_users_real_file(tmp_path: Path):
    """The agy global-settings case: a session mutates a user-owned file."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"theme": "dark"}))

    port = Materializer(MaterializationMode.PLAN)
    changed = port.merge_json(settings, {"theme": "dark", "hooks": {"PreToolUse": []}})

    assert changed is True
    assert json.loads(settings.read_text()) == {"theme": "dark"}  # untouched
    [entry] = port.plan.entries
    assert entry.kind is WriteKind.MERGE_JSON
    assert entry.detail == "+hooks"


def test_merge_json_unchanged_content_is_not_a_write(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"theme": "dark"}, indent=2) + "\n")

    port = Materializer(MaterializationMode.APPLY)
    changed = port.merge_json(settings, {"theme": "dark"})

    assert changed is False
    assert port.plan.entries == []


def test_merge_json_apply_writes(tmp_path: Path):
    settings = tmp_path / "nested" / "settings.json"

    port = Materializer(MaterializationMode.APPLY)
    port.merge_json(settings, {"hooks": {}})

    assert json.loads(settings.read_text()) == {"hooks": {}}


def test_duplicates_names_a_target_materialized_twice(tmp_path: Path):
    """R7 signal (b): two intents, one file. Today claude/AUTOMATE does this."""
    src = _skill_src(tmp_path)
    dest = tmp_path / "cache" / "plugin"

    port = Materializer(MaterializationMode.PLAN)
    port.copy_tree(src, dest)
    port.copy_tree(src, dest)

    assert port.plan.duplicates() == [dest]


def test_duplicates_empty_when_each_target_written_once(tmp_path: Path):
    port = Materializer(MaterializationMode.PLAN)
    port.write_text(tmp_path / "a", "x")
    port.write_text(tmp_path / "b", "y")

    assert port.plan.duplicates() == []


def test_duplicates_ignores_the_remove_then_write_sequence(tmp_path: Path):
    """Rebuild is remove-then-create on one path — that is one materialization."""
    target = tmp_path / "plugin"
    target.mkdir()

    port = Materializer(MaterializationMode.PLAN)
    port.remove_tree(target)
    port.mkdir(target)

    assert port.plan.duplicates() == []
