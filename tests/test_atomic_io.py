"""Tests for the canonical atomic-write helper (HATS-716).

Contract: ``atomic_write_bytes`` / ``atomic_write_text`` write a target via a
*unique* tmp file in the target's own directory followed by ``os.replace``, so:

- a crash/kill mid-write never leaves a torn or zero-byte target (the target
  always reflects either the complete old bytes or the complete new bytes);
- a failed write leaves no orphan tmp;
- concurrent writers never collide on the tmp name;
- default permissions match ``open(path, "w")`` (umask), with an optional
  explicit ``mode`` applied before the rename.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ai_hats.utils import atomic_io


def _tmp_siblings(path: Path) -> list[Path]:
    """Return leftover atomic-write tmp files next to ``path``."""
    return [p for p in path.parent.iterdir() if p.name.startswith(f".{path.name}.") and p.suffix == ".tmp"]


def test_atomic_write_text_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    atomic_io.atomic_write_text(target, "héllo\nworld\n")
    assert target.read_text() == "héllo\nworld\n"
    assert _tmp_siblings(target) == []


def test_atomic_write_bytes_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_io.atomic_write_bytes(target, b"\x00\x01\x02")
    assert target.read_bytes() == b"\x00\x01\x02"


def test_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c.txt"
    atomic_io.atomic_write_text(target, "deep")
    assert target.read_text() == "deep"


def test_crash_mid_write_preserves_original_and_leaves_no_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "card.yaml"
    target.write_text("ORIGINAL\n")

    def boom(src, dst):  # noqa: ANN001
        raise OSError("simulated crash before rename completes")

    monkeypatch.setattr(atomic_io.os, "replace", boom)

    with pytest.raises(OSError):
        atomic_io.atomic_write_text(target, "NEW CONTENT THAT MUST NOT LAND\n")

    # The pre-existing file is byte-identical — never truncated.
    assert target.read_text() == "ORIGINAL\n"
    # No tmp file left behind.
    assert _tmp_siblings(target) == []


def test_unique_tmp_under_concurrent_writers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "metrics.json"
    seen: list[str] = []
    real_replace = atomic_io.os.replace

    def record(src, dst):  # noqa: ANN001
        seen.append(str(src))
        real_replace(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", record)
    atomic_io.atomic_write_text(target, "1")
    atomic_io.atomic_write_text(target, "2")

    # Two writes used two distinct tmp paths (deterministic ".tmp" would collide).
    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert target.read_text() == "2"


def test_explicit_mode_sets_permissions(tmp_path: Path) -> None:
    target = tmp_path / "exec.sh"
    atomic_io.atomic_write_text(target, "#!/bin/sh\n", mode=0o755)
    assert (target.stat().st_mode & 0o777) == 0o755


def test_default_perms_respect_umask(tmp_path: Path) -> None:
    target = tmp_path / "respect-umask.txt"
    old = os.umask(0o022)
    try:
        atomic_io.atomic_write_text(target, "x")
        # open(path,'w') under umask 022 yields 0o644 — mkstemp's 0o600 must not leak.
        assert (target.stat().st_mode & 0o777) == 0o644
    finally:
        os.umask(old)
