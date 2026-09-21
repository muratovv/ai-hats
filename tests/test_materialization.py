"""The entry — what one write of a session carries, and what it can claim.

An entry is an instruction: its digest and size are derived from the payload
it holds, so it cannot claim bytes it does not have, and a private entry keeps
its bytes out of every record.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.fs_digest import dir_digest
from ai_hats.materialization import (
    MaterializationEntry,
    WriteKind,
    describe_copy_file,
    describe_merge_json,
    describe_mkdir,
    describe_private_text,
    describe_remove_tree,
    describe_symlink,
    describe_write_executable,
    describe_write_text,
    render_json,
)


def test_write_text_is_described_with_its_byte_size(tmp_path: Path):
    entry = describe_write_text(tmp_path / "session" / "prompt.md", "hello")

    assert entry.kind is WriteKind.WRITE_TEXT
    assert entry.size == 5
    assert entry.digest is not None and entry.bytes == b"hello"


def test_a_private_entry_hides_its_content_and_carries_no_digest(tmp_path: Path) -> None:
    content = '{"fixture": "private credentials"}'

    entry = describe_private_text(tmp_path / "auth.json", content)

    assert entry.kind is WriteKind.WRITE_TEXT and entry.private
    assert entry.digest is None
    assert entry.size == len(content)
    assert content not in repr(entry)


def test_write_executable_is_the_text_entry_of_another_kind(tmp_path: Path):
    entry = describe_write_executable(tmp_path / "bin" / "rack", "#!/bin/sh\nexit 0\n")

    assert entry.kind is WriteKind.WRITE_EXECUTABLE
    assert entry.size == 17


def test_a_tree_entry_carries_the_digest_it_was_handed_and_no_size(tmp_path: Path):
    src = tmp_path / "src-skill"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text("body")
    digest = dir_digest(src)

    entry = MaterializationEntry(
        kind=WriteKind.COPY_TREE, target=tmp_path / "s", source=src, tree_digest=digest
    )

    assert entry.digest == digest
    assert entry.size is None, "a tree's size is a fact of application, not of the entry"


def test_a_copied_file_names_its_source_and_may_be_private(tmp_path: Path):
    entry = describe_copy_file(tmp_path / "auth.json", tmp_path / "s" / "auth.json", private=True)

    assert entry.kind is WriteKind.COPY_FILE and entry.source == tmp_path / "auth.json"
    assert entry.digest is None and entry.size is None


def test_the_shapes_that_carry_no_bytes(tmp_path: Path):
    for entry in (
        describe_symlink(tmp_path / "a", tmp_path / "b"),
        describe_mkdir(tmp_path / "d"),
        describe_remove_tree(tmp_path / "gone"),
    ):
        assert entry.bytes is None and entry.size is None and entry.digest is None


def test_merge_json_carries_what_ai_hats_adds_as_its_bytes(tmp_path: Path):
    entry = describe_merge_json(tmp_path / "settings.json", {"hooks": {"PreToolUse": []}})

    assert entry.kind is WriteKind.MERGE_JSON
    assert entry.bytes == render_json({"hooks": {"PreToolUse": []}}).encode()
    assert entry.data == {"hooks": {"PreToolUse": []}}


def test_the_callers_dict_stays_the_callers(tmp_path: Path):
    data = {"hooks": {}}
    entry = describe_merge_json(tmp_path / "settings.json", data)
    data["hooks"]["later"] = []

    assert entry.data == {"hooks": {}}
    with pytest.raises(TypeError):
        entry.data["x"] = 1  # type: ignore[index]


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(kind=WriteKind.WRITE_TEXT, content="x", source=Path("/s")),
        dict(kind=WriteKind.COPY_TREE),
        dict(kind=WriteKind.MKDIR, content="x"),
        dict(kind=WriteKind.WRITE_TEXT, content="x", tree_digest="d"),
        dict(kind=WriteKind.SYMLINK, source=Path("/s"), private=True),
    ],
    ids=[
        "two payloads",
        "no payload",
        "payload on a bare kind",
        "digest off a tree",
        "private link",
    ],
)
def test_an_entry_carries_exactly_the_payload_of_its_kind(tmp_path: Path, kwargs):
    with pytest.raises(ValueError):
        MaterializationEntry(target=tmp_path / "t", **kwargs)
