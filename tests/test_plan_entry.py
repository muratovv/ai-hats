"""A materialization entry is an instruction: it carries the bytes it writes.

``digest`` and ``size`` are derived from those bytes, never stored, so an entry
cannot claim bytes it does not hold (ADR-0036 D1, D3).
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import pytest

from ai_hats.materialization import MaterializationEntry, WriteKind, render_json


def test_a_text_entry_derives_digest_and_size_from_its_content():
    entry = MaterializationEntry(WriteKind.WRITE_TEXT, Path("/r/prompt.md"), content="héllo\n")
    assert entry.size == len("héllo\n".encode())
    assert entry.digest == hashlib.sha256("héllo\n".encode()).hexdigest()
    assert {f.name for f in dataclasses.fields(entry)}.isdisjoint({"digest", "size"})


def test_a_private_entry_holds_its_bytes_but_reports_no_digest():
    entry = MaterializationEntry(
        WriteKind.WRITE_TEXT, Path("/r/auth.json"), content="secret", private=True
    )
    assert entry.content == "secret"
    assert entry.digest is None
    assert entry.size == 6


def test_a_json_entry_digests_the_rendered_document():
    entry = MaterializationEntry(WriteKind.MERGE_JSON, Path("/r/s.json"), data={"hooks": {}})
    rendered = render_json({"hooks": {}}).encode()
    assert (entry.size, entry.digest) == (len(rendered), hashlib.sha256(rendered).hexdigest())


def test_a_tree_entry_carries_its_source_digest_and_no_size():
    entry = MaterializationEntry(
        WriteKind.COPY_TREE, Path("/r/skills/x"), source=Path("/lib/x"), tree_digest="ab" * 32
    )
    assert entry.digest == "ab" * 32
    assert entry.size is None


def test_a_file_copy_holds_no_bytes_and_may_be_private():
    entry = MaterializationEntry(
        WriteKind.COPY_FILE, Path("/r/auth.json"), source=Path("/home/u/.codex/auth.json")
    )
    assert (entry.digest, entry.size) == (None, None), "the bytes are read at application"
    private = dataclasses.replace(entry, private=True)
    assert private.private and private.digest is None


@pytest.mark.parametrize(
    "kind, payload",
    [
        (WriteKind.WRITE_TEXT, {}),
        (WriteKind.WRITE_EXECUTABLE, {"source": Path("/x")}),
        (WriteKind.COPY_TREE, {"content": "x"}),
        (WriteKind.COPY_FILE, {"content": "x"}),
        (WriteKind.COPY_FILE, {"source": Path("/x"), "tree_digest": "ab" * 32}),
        (WriteKind.SYMLINK, {}),
        (WriteKind.MERGE_JSON, {"content": "{}"}),
        (WriteKind.MKDIR, {"content": "x"}),
        (WriteKind.REMOVE_TREE, {"data": {}}),
        (WriteKind.COPY_TREE, {"source": Path("/x"), "private": True}),
    ],
)
def test_a_payload_that_does_not_fit_its_kind_is_refused(kind, payload):
    with pytest.raises(ValueError):
        MaterializationEntry(kind, Path("/r/t"), **payload)
