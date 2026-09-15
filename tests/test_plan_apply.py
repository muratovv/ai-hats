"""Applying a materialization plan is mechanical and idempotent (ADR-0036 D3).

The counter is on the filesystem primitives themselves: a second application
of an applied plan reaches none of them, and one changed entry reaches exactly
what that entry needs. Planning refusals fire before any primitive.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
from pathlib import Path

import pytest

from ai_hats.fs_digest import dir_digest
from ai_hats.materialization import MaterializationEntry, WriteKind
from ai_hats.surfaces import apply, validate
from ai_hats.surfaces.plan import (
    CompositionPlan,
    DuplicateTargets,
    EscapeUndeclared,
    Hooks,
    Launch,
    MaterializationPlan,
    Outcome,
    Prompt,
    PromptBlock,
    PromptMember,
    StalePlan,
    UnmergeableTarget,
)
from ai_hats.session_artifacts import RunMode, SessionPolicy

_PRIMITIVES = (
    (Path, "write_text"),
    (Path, "write_bytes"),
    (Path, "mkdir"),
    (Path, "symlink_to"),
    (Path, "unlink"),
    (Path, "chmod"),
    (shutil, "copy2"),
    (shutil, "copytree"),
    (shutil, "rmtree"),
    (os, "replace"),
    (os, "rename"),
)


@pytest.fixture
def writes(monkeypatch) -> list[str]:
    """Every filesystem write primitive reached, by name."""
    reached: list[str] = []
    for owner, name in _PRIMITIVES:
        original = getattr(owner, name)

        def spy(*args, _name=name, _original=original, **kwargs):
            # filelock re-mkdirs the lock's directory on every acquire; an
            # existing directory is not a write, so only a creation counts.
            if _name != "mkdir" or not Path(args[0]).exists():
                reached.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(owner, name, spy)
    return reached


def _composition() -> CompositionPlan:
    return CompositionPlan(
        identity="t",
        prompt=Prompt(blocks=(PromptBlock(None, (PromptMember("t::prompt", "# t\n", None),)),)),
        skills=(),
        hooks=Hooks((), ()),
        trace=(),
    )


def _plan(root: Path, *entries: MaterializationEntry) -> MaterializationPlan:
    composition = _composition()
    return MaterializationPlan(
        composition=composition,
        prompt=composition.prompt,
        surface="claude",
        run_mode=RunMode.HITL,
        policy=SessionPolicy(),
        root=root,
        entries=entries,
        env={},
        launch=Launch(args=("claude",), sdk_options=None),
    )


def test_the_surface_prompt_opens_with_the_composition_s_blocks_and_may_add_its_own(tmp_path):
    plan = _plan(tmp_path / "s")
    index = PromptBlock("AVAILABLE SKILLS", (PromptMember("claude::skill_index", "- x", None),))
    extended = dataclasses.replace(plan, prompt=Prompt((*plan.composition.prompt.blocks, index)))
    assert extended.prompt.text.startswith(plan.composition.prompt.text)
    assert extended.digest != plan.digest

    foreign = Prompt((index,))
    with pytest.raises(ValueError, match="composition"):
        dataclasses.replace(plan, prompt=foreign)


def _skill(tmp_path: Path) -> Path:
    src = tmp_path / "lib" / "skills" / "s"
    (src / "hooks").mkdir(parents=True)
    (src / "SKILL.md").write_text("# s\n")
    (src / "hooks" / "gate.sh").write_text("#!/bin/sh\nexit 0\n")
    (src / "hooks" / "gate.sh").chmod(0o755)
    return src


def _seven_kinds(tmp_path: Path) -> MaterializationPlan:
    root = tmp_path / "session"
    src = _skill(tmp_path)
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "auth.json").write_text("{}")
    (root / "stale").mkdir(parents=True)
    (root / "stale" / "old.txt").write_text("gone")
    return _plan(
        root,
        MaterializationEntry(WriteKind.MKDIR, root),
        MaterializationEntry(WriteKind.REMOVE_TREE, root / "stale"),
        MaterializationEntry(WriteKind.WRITE_TEXT, root / "prompt.md", content="# hello\n"),
        MaterializationEntry(
            WriteKind.WRITE_EXECUTABLE, root / "bin" / "rack", content="#!/bin/sh\nexit 0\n"
        ),
        MaterializationEntry(
            WriteKind.COPY_TREE, root / "skills" / "s", source=src, tree_digest=dir_digest(src)
        ),
        MaterializationEntry(
            WriteKind.WRITE_TEXT, root / "skills" / "s" / "SKILL.md", content="# s (rendered)\n"
        ),
        MaterializationEntry(
            WriteKind.SYMLINK, root / "auth.json", source=tmp_path / "home" / "auth.json"
        ),
        MaterializationEntry(WriteKind.MERGE_JSON, root / "settings.json", data={"hooks": {}}),
        MaterializationEntry(
            WriteKind.WRITE_TEXT, root / "private.json", content="secret", private=True
        ),
    )


def test_apply_performs_every_kind_in_order(tmp_path: Path):
    plan = _seven_kinds(tmp_path)
    root = plan.root

    apply(plan)

    assert not (root / "stale").exists()
    assert (root / "prompt.md").read_text() == "# hello\n"
    assert (root / "bin" / "rack").stat().st_mode & 0o111
    assert (root / "skills" / "s" / "hooks" / "gate.sh").stat().st_mode & 0o111
    assert (root / "skills" / "s" / "SKILL.md").read_text() == "# s (rendered)\n"
    assert (root / "auth.json").is_symlink()
    assert json.loads((root / "settings.json").read_text()) == {"hooks": {}}
    assert (root / "private.json").stat().st_mode & 0o777 == 0o600


def test_a_second_application_reaches_no_primitive(tmp_path: Path, writes: list[str]):
    plan = _seven_kinds(tmp_path)
    apply(plan)
    assert writes, "the first application writes — or the counter is broken"
    writes.clear()

    apply(plan)

    assert writes == []


def test_the_application_says_what_it_did_to_each_entry(tmp_path: Path):
    """The outcome is a value beside the plan: the record and a dry-run
    ``--materialize`` note read what happened from it, not from the disk."""
    plan = _seven_kinds(tmp_path)

    first = apply(plan)

    assert [a.entry for a in first.entries] == list(plan.entries)
    assert [a.outcome for a in first.entries] == [
        Outcome.UNCHANGED,  # mkdir — the root stood already, planted with the stale dir
        Outcome.REMOVED,  # remove_tree
        Outcome.WRITTEN,  # write_text
        Outcome.WRITTEN,  # write_executable
        Outcome.WRITTEN,  # copy_tree
        Outcome.WRITTEN,  # write_text over the tree
        Outcome.WRITTEN,  # symlink
        Outcome.WRITTEN,  # merge_json
        Outcome.WRITTEN,  # private write_text
    ]
    tree = next(a for a in first.entries if a.entry.kind is WriteKind.COPY_TREE)
    assert tree.files == 2, "SKILL.md and hooks/gate.sh — a fact of application, not of planning"
    assert [a.files for a in first.entries if a.entry.kind is not WriteKind.COPY_TREE] == [None] * 8
    assert first.changed

    second = apply(plan)

    assert [a.outcome for a in second.entries] == [
        Outcome.UNCHANGED,
        Outcome.ABSENT,
        *[Outcome.UNCHANGED] * 7,
    ]
    assert not second.changed
    assert next(a for a in second.entries if a.entry.kind is WriteKind.COPY_TREE).files == 2


def test_one_changed_entry_reaches_exactly_its_own_primitive(tmp_path: Path, writes: list[str]):
    plan = _seven_kinds(tmp_path)
    apply(plan)
    writes.clear()
    changed = [
        MaterializationEntry(WriteKind.WRITE_TEXT, plan.root / "prompt.md", content="# other\n")
        if e.target == plan.root / "prompt.md"
        else e
        for e in plan.entries
    ]

    apply(MaterializationPlan(**{**vars(plan), "entries": tuple(changed)}))

    assert writes == ["write_bytes"]
    assert (plan.root / "prompt.md").read_text() == "# other\n"


def test_a_changed_source_file_resyncs_that_file_alone(tmp_path: Path, writes: list[str]):
    plan = _seven_kinds(tmp_path)
    apply(plan)
    src = next(e.source for e in plan.entries if e.kind is WriteKind.COPY_TREE)
    (src / "hooks" / "gate.sh").write_text("#!/bin/sh\nexit 2\n")
    entries = tuple(
        MaterializationEntry(
            WriteKind.COPY_TREE, e.target, source=e.source, tree_digest=dir_digest(src)
        )
        if e.kind is WriteKind.COPY_TREE
        else e
        for e in plan.entries
    )
    writes.clear()

    apply(MaterializationPlan(**{**vars(plan), "entries": entries}))

    assert writes == ["copy2"]
    assert (plan.root / "skills" / "s" / "hooks" / "gate.sh").read_text() == "#!/bin/sh\nexit 2\n"


def test_a_source_tree_that_no_longer_matches_the_plan_is_refused(tmp_path: Path, writes):
    plan = _seven_kinds(tmp_path)
    src = next(e.source for e in plan.entries if e.kind is WriteKind.COPY_TREE)
    (src / "extra.txt").write_text("edited after planning")

    with pytest.raises(StalePlan):
        apply(plan)

    assert "copy2" not in writes and "copytree" not in writes


def test_two_entries_creating_one_target_are_refused_before_any_write(tmp_path: Path, writes):
    root = tmp_path / "s"
    plan = _plan(
        root,
        MaterializationEntry(WriteKind.WRITE_TEXT, root / "a", content="1"),
        MaterializationEntry(WriteKind.WRITE_TEXT, root / "a", content="2"),
    )
    with pytest.raises(DuplicateTargets) as refused:
        apply(plan)
    assert str(root / "a") in str(refused.value)
    assert writes == []


def test_remove_then_create_on_one_target_is_not_a_duplicate(tmp_path: Path):
    root = tmp_path / "s"
    validate(
        _plan(
            root,
            MaterializationEntry(WriteKind.REMOVE_TREE, root / "plugin"),
            MaterializationEntry(WriteKind.MKDIR, root / "plugin"),
        )
    )


def test_a_target_outside_the_root_needs_a_declared_escape(tmp_path: Path, writes):
    root = tmp_path / "s"
    outside = tmp_path / "home" / "settings.json"
    with pytest.raises(EscapeUndeclared):
        validate(_plan(root, MaterializationEntry(WriteKind.MERGE_JSON, outside, data={})))
    validate(_plan(root, MaterializationEntry(WriteKind.MERGE_JSON, outside, data={}, escape=True)))
    assert writes == []


def test_a_stray_file_in_a_synced_tree_is_removed(tmp_path: Path, writes: list[str]):
    plan = _seven_kinds(tmp_path)
    apply(plan)
    junk = plan.root / "skills" / "s" / "junk.txt"
    junk.write_text("left by someone else")
    writes.clear()

    apply(plan)

    assert writes == ["unlink"]
    assert not junk.exists()
    assert (plan.root / "skills" / "s" / "SKILL.md").read_text() == "# s (rendered)\n"


def test_a_file_copy_reads_its_source_at_application(tmp_path: Path, writes: list[str]):
    root = tmp_path / "s"
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text('{"token": "t1"}')
    plan = _plan(
        root,
        MaterializationEntry(
            WriteKind.COPY_FILE, root / "auth.json", source=home / "auth.json", private=True
        ),
    )

    apply(plan)
    assert (root / "auth.json").read_text() == '{"token": "t1"}'
    assert (root / "auth.json").stat().st_mode & 0o777 == 0o600
    writes.clear()

    apply(plan)
    assert writes == []

    (home / "auth.json").write_text('{"token": "t2"}')
    apply(plan)
    assert (root / "auth.json").read_text() == '{"token": "t2"}'


def test_a_file_copy_whose_source_is_gone_is_refused_before_any_write(tmp_path: Path, writes):
    root = tmp_path / "s"
    plan = _plan(
        root,
        MaterializationEntry(WriteKind.WRITE_TEXT, root / "prompt.md", content="# hi\n"),
        MaterializationEntry(WriteKind.COPY_FILE, root / "auth.json", source=tmp_path / "gone"),
    )
    with pytest.raises(StalePlan):
        apply(plan)
    assert writes == []
    assert not (root / "prompt.md").exists()


def _managed(command: str, tag: str = "ai-hats:dispatcher") -> dict:
    return {"matcher": "*", "command": command, "_ai_hats_managed": tag}


def test_merge_json_adds_to_a_user_owned_document_and_leaves_the_rest_alone(
    tmp_path: Path, writes: list[str]
):
    root = tmp_path / "s"
    settings = tmp_path / "home" / "settings.json"
    settings.parent.mkdir()
    theirs = {"matcher": "Edit", "command": "their-own-hook"}
    settings.write_text(
        json.dumps({"theme": "dark", "hooks": {"PreToolUse": [theirs], "Stop": [theirs]}})
    )
    patch = {"hooks": {"PreToolUse": [_managed("v1")], "PostToolUse": [_managed("v1")]}}
    plan = _plan(
        root, MaterializationEntry(WriteKind.MERGE_JSON, settings, data=patch, escape=True)
    )

    apply(plan)
    merged = json.loads(settings.read_text())
    assert merged["theme"] == "dark"
    assert merged["hooks"]["PreToolUse"] == [theirs, _managed("v1")]
    assert merged["hooks"]["PostToolUse"] == [_managed("v1")]
    assert merged["hooks"]["Stop"] == [theirs]
    writes.clear()

    apply(plan)
    assert writes == []

    newer = {"hooks": {"PreToolUse": [_managed("v2")]}}
    apply(
        _plan(root, MaterializationEntry(WriteKind.MERGE_JSON, settings, data=newer, escape=True))
    )
    merged = json.loads(settings.read_text())
    assert merged["hooks"]["PreToolUse"] == [theirs, _managed("v2")], "replaced by its tag"
    assert merged["hooks"]["PostToolUse"] == [_managed("v1")], "a key the patch omits is kept"


def test_merge_json_replaces_a_managed_entry_the_patch_dropped_but_never_a_foreign_one(
    tmp_path: Path,
):
    root = tmp_path / "s"
    settings = root / "settings.json"
    root.mkdir()
    theirs = {"matcher": "Edit", "command": "their-own-hook"}
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [_managed("a", "ai-hats:a"), theirs]}}))

    apply(
        _plan(
            root,
            MaterializationEntry(
                WriteKind.MERGE_JSON,
                settings,
                data={"hooks": {"PreToolUse": [_managed("b", "ai-hats:b")]}},
            ),
        )
    )

    merged = json.loads(settings.read_text())
    assert merged["hooks"]["PreToolUse"] == [theirs, _managed("b", "ai-hats:b")]


def test_merge_json_replaces_a_plain_list_and_a_scalar_wholesale(tmp_path: Path):
    root = tmp_path / "s"
    config = root / "opencode.json"
    root.mkdir()
    config.write_text(json.dumps({"agent": {"x": {"tools": ["a", "b"], "mode": "primary"}}}))

    apply(
        _plan(
            root,
            MaterializationEntry(
                WriteKind.MERGE_JSON, config, data={"agent": {"x": {"tools": ["c"], "mode": "sub"}}}
            ),
        )
    )

    assert json.loads(config.read_text()) == {"agent": {"x": {"tools": ["c"], "mode": "sub"}}}


def test_merge_json_creates_the_document_when_there_is_none(tmp_path: Path):
    root = tmp_path / "s"
    apply(_plan(root, MaterializationEntry(WriteKind.MERGE_JSON, root / "c.json", data={"a": 1})))
    assert json.loads((root / "c.json").read_text()) == {"a": 1}


def test_merge_json_refuses_a_target_that_is_not_a_json_object_before_any_write(
    tmp_path: Path, writes
):
    root = tmp_path / "s"
    root.mkdir()
    (root / "broken.json").write_text("{not json")
    plan = _plan(
        root,
        MaterializationEntry(WriteKind.WRITE_TEXT, root / "prompt.md", content="# hi\n"),
        MaterializationEntry(WriteKind.MERGE_JSON, root / "broken.json", data={"a": 1}),
    )
    writes.clear()
    with pytest.raises(UnmergeableTarget):
        apply(plan)
    assert writes == []
    assert (root / "broken.json").read_text() == "{not json", "a broken user file is not clobbered"


def test_a_symlink_pointing_elsewhere_is_repointed(tmp_path: Path):
    root = tmp_path / "s"
    (tmp_path / "a").write_text("a")
    (tmp_path / "b").write_text("b")
    link = root / "auth.json"
    apply(_plan(root, MaterializationEntry(WriteKind.SYMLINK, link, source=tmp_path / "a")))

    apply(_plan(root, MaterializationEntry(WriteKind.SYMLINK, link, source=tmp_path / "b")))

    assert link.readlink() == tmp_path / "b"


def test_remove_tree_ensures_absence_of_a_symlink_and_of_a_plain_file(tmp_path: Path):
    root = tmp_path / "s"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "keep.txt").write_text("keep")
    (root / "link").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    (root / "file.txt").write_text("x")

    apply(
        _plan(
            root,
            MaterializationEntry(WriteKind.REMOVE_TREE, root / "link"),
            MaterializationEntry(WriteKind.REMOVE_TREE, root / "file.txt"),
        )
    )

    assert not (root / "link").is_symlink() and not (root / "file.txt").exists()
    assert (tmp_path / "elsewhere" / "keep.txt").read_text() == "keep", "the link, not its target"


def test_a_stale_source_tree_is_refused_before_any_entry_is_performed(tmp_path: Path, writes):
    plan = _seven_kinds(tmp_path)
    src = next(e.source for e in plan.entries if e.kind is WriteKind.COPY_TREE)
    (src / "extra.txt").write_text("edited after planning")
    (plan.root / "stale").rename(tmp_path / "keep-stale")  # so the counter sees only apply's writes
    (plan.root / "stale").mkdir()
    writes.clear()

    with pytest.raises(StalePlan):
        apply(plan)

    assert writes == []
    assert not (plan.root / "prompt.md").exists()


def test_a_symlink_at_a_file_target_is_replaced_never_written_through(tmp_path: Path):
    root = tmp_path / "s"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched")
    (root / "p.md").symlink_to(outside)

    apply(_plan(root, MaterializationEntry(WriteKind.WRITE_TEXT, root / "p.md", content="new")))

    assert outside.read_text() == "untouched"
    assert not (root / "p.md").is_symlink() and (root / "p.md").read_text() == "new"


def test_a_symlink_at_a_tree_target_is_replaced_never_synced_through(tmp_path: Path):
    root = tmp_path / "s"
    root.mkdir()
    src = _skill(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.txt").write_text("mine")
    (root / "skills").mkdir()
    (root / "skills" / "s").symlink_to(elsewhere, target_is_directory=True)

    apply(
        _plan(
            root,
            MaterializationEntry(
                WriteKind.COPY_TREE, root / "skills" / "s", source=src, tree_digest=dir_digest(src)
            ),
        )
    )

    assert (elsewhere / "precious.txt").read_text() == "mine"
    assert not (root / "skills" / "s").is_symlink()
    assert (root / "skills" / "s" / "SKILL.md").read_text() == "# s\n"


def test_a_directory_where_a_file_belongs_gives_way_to_the_file(tmp_path: Path):
    root = tmp_path / "s"
    src = _skill(tmp_path)
    (root / "skills" / "s" / "SKILL.md").mkdir(parents=True)

    apply(
        _plan(
            root,
            MaterializationEntry(
                WriteKind.COPY_TREE, root / "skills" / "s", source=src, tree_digest=dir_digest(src)
            ),
        )
    )

    assert (root / "skills" / "s" / "SKILL.md").is_file()


def test_a_dot_dot_target_cannot_pass_as_inside_the_root(tmp_path: Path):
    root = tmp_path / "s"
    sneaky = root / ".." / "outside.json"
    with pytest.raises(EscapeUndeclared):
        validate(_plan(root, MaterializationEntry(WriteKind.MERGE_JSON, sneaky, data={})))


def test_a_later_entry_shadows_a_tree_file_but_an_earlier_one_is_overwritten_by_the_sync(
    tmp_path: Path,
):
    root = tmp_path / "s"
    src = _skill(tmp_path)
    tree = MaterializationEntry(
        WriteKind.COPY_TREE, root / "skills" / "s", source=src, tree_digest=dir_digest(src)
    )
    rendered = MaterializationEntry(
        WriteKind.WRITE_TEXT, root / "skills" / "s" / "SKILL.md", content="# rendered\n"
    )

    apply(_plan(root, rendered, tree))
    assert (root / "skills" / "s" / "SKILL.md").read_text() == "# s\n", "the later entry wins"

    apply(_plan(root, tree, rendered))
    assert (root / "skills" / "s" / "SKILL.md").read_text() == "# rendered\n"


def test_two_spellings_of_one_target_are_one_duplicate(tmp_path: Path):
    root = tmp_path / "s"
    with pytest.raises(DuplicateTargets):
        validate(
            _plan(
                root,
                MaterializationEntry(WriteKind.WRITE_TEXT, root / "a", content="1"),
                MaterializationEntry(WriteKind.WRITE_TEXT, root / "x" / ".." / "a", content="2"),
            )
        )


def test_a_tree_sync_keeps_empty_dirs_follows_dir_links_and_drops_stray_dirs(tmp_path: Path):
    src = _skill(tmp_path)
    (src / "empty").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "shared.txt").write_text("via link")
    (src / "linked").symlink_to(elsewhere, target_is_directory=True)
    root = tmp_path / "s"
    dest = root / "skills" / "s"
    (dest / "stale-dir").mkdir(parents=True)
    (dest / "stale-dir" / "old.txt").write_text("gone")
    plan = _plan(
        root,
        MaterializationEntry(WriteKind.COPY_TREE, dest, source=src, tree_digest=dir_digest(src)),
    )

    apply(plan)

    assert (dest / "empty").is_dir(), "copytree created empty directories; the sync must too"
    assert (dest / "linked" / "shared.txt").read_text() == "via link"
    assert not (dest / "linked").is_symlink(), "copied as a directory, as copytree does"
    assert not (dest / "stale-dir").exists()


# ── the value's digest ──────────────────────────────────────────────────────


def test_the_plan_digest_folds_an_entry_by_the_entry_s_own_digest(tmp_path: Path):
    root = tmp_path / "s"
    one = _plan(root, MaterializationEntry(WriteKind.WRITE_TEXT, root / "p", content="1"))
    two = _plan(root, MaterializationEntry(WriteKind.WRITE_TEXT, root / "p", content="2"))
    assert one.digest != two.digest

    secret = _plan(
        root, MaterializationEntry(WriteKind.WRITE_TEXT, root / "k", content="s1", private=True)
    )
    other = _plan(
        root, MaterializationEntry(WriteKind.WRITE_TEXT, root / "k", content="s2", private=True)
    )
    assert secret != other, "== still compares the bytes"
    assert secret.digest == other.digest, "a private entry puts no digest of its bytes anywhere"


def test_a_float_among_the_sdk_options_digests():
    assert (
        Launch(None, {"max_budget_usd": 1.5}).digest != Launch(None, {"max_budget_usd": 2.5}).digest
    )
