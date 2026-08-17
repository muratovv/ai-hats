"""Materialization port: one interface, two implementations (HATS-1211).

The contract tests run against BOTH implementations — that is the point. If the
record can differ by implementation, the dry-run report is a guess. Disk effects
are asserted separately, per implementation, because that is the only place the
two are allowed to differ.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.materialization import (
    ApplyMaterializer,
    Materializer,
    PlanMaterializer,
    WriteKind,
)

IMPLEMENTATIONS = [ApplyMaterializer, PlanMaterializer]


@pytest.fixture(params=IMPLEMENTATIONS, ids=lambda c: c.__name__)
def port(request) -> Materializer:
    return request.param()


def _skill_src(root: Path) -> Path:
    src = root / "src-skill"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text("body")  # 4 bytes
    (src / "scripts" / "run.sh").write_text("echo")  # 4 bytes
    return src


def _session_build(port: Materializer, cache: Path, src: Path) -> None:
    """A realistic build: four category handlers over one cache dir, plus a rebuild."""
    for _ in range(4):
        port.mkdir(cache)  # every category handler mkdirs the cache dir
    port.write_text(cache / "prompt.md", "role text")
    plugin = cache / "plugin"
    port.remove_tree(plugin)
    port.mkdir(plugin)
    port.copy_tree(src, plugin / "skills")
    port.merge_json(cache / "settings.json", {"hooks": {"PreToolUse": []}})


# --- the contract: both implementations describe the world identically ---


def test_both_implementations_record_the_same_build(tmp_path: Path):
    src = _skill_src(tmp_path)

    applied = ApplyMaterializer()
    _session_build(applied, tmp_path / "a" / "cache", src)

    planned = PlanMaterializer()
    _session_build(planned, tmp_path / "b" / "cache", src)

    def shape(plan):
        return [
            (e.kind, e.target.relative_to(tmp_path).parts[1:], e.size, e.file_count, e.detail)
            for e in plan.entries
        ]

    assert shape(planned.plan) == shape(applied.plan)


def test_write_text_is_recorded_with_its_byte_size(port: Materializer, tmp_path: Path):
    port.write_text(tmp_path / "session" / "prompt.md", "hello")

    [entry] = port.plan.entries
    assert entry.kind is WriteKind.WRITE_TEXT
    assert entry.size == 5


def test_copy_tree_is_recorded_with_file_count_and_bytes(port: Materializer, tmp_path: Path):
    src = _skill_src(tmp_path)
    port.copy_tree(src, tmp_path / "cache" / "skills" / "s")

    [entry] = port.plan.entries
    assert entry.kind is WriteKind.COPY_TREE
    assert entry.source == src
    assert (entry.file_count, entry.size) == (2, 8)


def test_symlink_is_recorded_without_dry_run_writes(port: Materializer, tmp_path: Path):
    source = tmp_path / "user-home" / "auth.json"
    source.parent.mkdir()
    source.write_text("auth")
    target = tmp_path / "session" / "auth.json"

    port.symlink(source, target)

    [entry] = port.plan.entries
    assert entry.kind is WriteKind.SYMLINK
    assert entry.source == source
    assert entry.target == target
    assert target.is_symlink() is isinstance(port, ApplyMaterializer)


def test_repeated_mkdir_of_one_dir_is_recorded_once(port: Materializer, tmp_path: Path):
    target = tmp_path / "cache" / "sessions" / "sid"
    port.mkdir(target)
    port.mkdir(target)

    assert [e.kind for e in port.plan.entries] == [WriteKind.MKDIR]


def test_mkdir_of_an_existing_dir_is_not_recorded(port: Materializer, tmp_path: Path):
    port.mkdir(tmp_path)

    assert port.plan.entries == []


def test_mkdir_after_remove_is_recorded_again(port: Materializer, tmp_path: Path):
    """plugin_dir rebuilds: rmtree then mkdir. The dir is genuinely re-created."""
    target = tmp_path / "plugin"
    target.mkdir()

    port.remove_tree(target)
    port.mkdir(target)

    assert [e.kind for e in port.plan.entries] == [WriteKind.REMOVE_TREE, WriteKind.MKDIR]


def test_write_text_does_not_make_its_parent_a_separate_mkdir(port: Materializer, tmp_path: Path):
    """Both create parents implicitly; neither may report an extra MKDIR for it."""
    nested = tmp_path / "cache" / "rules"
    port.write_text(nested / "GEMINI.md", "x")
    port.mkdir(nested)

    assert [e.kind for e in port.plan.entries] == [WriteKind.WRITE_TEXT]


def test_remove_tree_of_a_missing_target_is_not_recorded(port: Materializer, tmp_path: Path):
    port.remove_tree(tmp_path / "never-existed")

    assert port.plan.entries == []


def test_merge_json_records_the_key_diff(port: Materializer, tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"theme": "dark"}))

    changed = port.merge_json(settings, {"theme": "dark", "hooks": {"PreToolUse": []}})

    assert changed is True
    [entry] = port.plan.entries
    assert entry.kind is WriteKind.MERGE_JSON
    assert entry.detail == "+hooks"


def test_merge_json_with_identical_content_is_not_a_write(port: Materializer, tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"theme": "dark"}, indent=2) + "\n")

    assert port.merge_json(settings, {"theme": "dark"}) is False
    assert port.plan.entries == []


def test_duplicates_names_a_target_materialized_twice(port: Materializer, tmp_path: Path):
    """R7 signal (b): two intents, one file. Today claude/AUTOMATE does this."""
    target = tmp_path / "cache" / "prompt.md"

    port.write_text(target, "role text")
    port.write_text(target, "role text")

    assert port.plan.duplicates() == [target]


def test_duplicates_ignores_the_remove_then_create_rebuild(port: Materializer, tmp_path: Path):
    target = tmp_path / "plugin"
    target.mkdir()

    port.remove_tree(target)
    port.mkdir(target)

    assert port.plan.duplicates() == []


# --- disk effects: the only place the implementations may differ ---


def test_apply_writes_to_disk(tmp_path: Path):
    src = _skill_src(tmp_path)
    port = ApplyMaterializer()

    port.write_text(tmp_path / "cache" / "prompt.md", "hello")
    port.copy_tree(src, tmp_path / "cache" / "skills")
    port.merge_json(tmp_path / "cache" / "settings.json", {"hooks": {}})

    assert (tmp_path / "cache" / "prompt.md").read_text() == "hello"
    assert (tmp_path / "cache" / "skills" / "scripts" / "run.sh").read_text() == "echo"
    assert json.loads((tmp_path / "cache" / "settings.json").read_text()) == {"hooks": {}}


def test_apply_removes(tmp_path: Path):
    doomed = tmp_path / "plugin"
    (doomed / "skills").mkdir(parents=True)

    ApplyMaterializer().remove_tree(doomed)

    assert not doomed.exists()


def test_plan_leaves_the_filesystem_byte_identical(tmp_path: Path):
    """The guarantee, in miniature: a full build must not move a single byte."""
    src = _skill_src(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "plugin").mkdir()
    (cache / "plugin" / "stale.txt").write_text("from a previous session")

    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    _session_build(PlanMaterializer(), cache, src)

    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
