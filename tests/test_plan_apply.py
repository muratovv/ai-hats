"""Applying a materialization plan is mechanical and idempotent (ADR-0036 D3).

The counter is on the filesystem primitives themselves: a second application
of an applied plan reaches none of them, and one changed entry reaches exactly
what that entry needs. Planning refusals fire before any primitive.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from ai_hats.fs_digest import dir_digest
from ai_hats.materialization import MaterializationEntry, WriteKind
from ai_hats.plan import (
    CompositionPlan,
    Consent,
    DuplicateTargets,
    EscapeUndeclared,
    Hooks,
    InvalidConsentSelector,
    Launch,
    MaterializationPlan,
    Prompt,
    PromptMember,
    StalePlan,
    apply,
    validate,
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


def _composition(consent: tuple[Consent, ...] = ()) -> CompositionPlan:
    return CompositionPlan(
        identity="t",
        prompt=Prompt(text="# t\n", members=(PromptMember("body", "t::prompt"),)),
        skills=(),
        hooks=Hooks((), (), (), ()),
        consent=consent,
        trace=(),
        diagnostics=(),
    )


def _plan(root: Path, *entries: MaterializationEntry, consent=()) -> MaterializationPlan:
    return MaterializationPlan(
        composition=_composition(consent),
        surface="claude",
        run_mode=RunMode.HITL,
        policy=SessionPolicy(),
        root=root,
        entries=entries,
        env={},
        launch=Launch(args=("claude",), sdk_options=None),
    )


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

    assert writes == ["write_text"]
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


def test_a_consent_selector_the_adapter_cannot_read_is_refused(tmp_path: Path, writes):
    root = tmp_path / "s"
    bad = Consent("rack.transition", "plan->", "plan", None, "t", None)
    with pytest.raises(InvalidConsentSelector) as refused:
        apply(_plan(root, consent=(bad,)))
    assert "plan->" in str(refused.value)
    assert writes == []
    validate(_plan(root, consent=(Consent("rack.transition", "->done", None, "done", "t", None),)))


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
