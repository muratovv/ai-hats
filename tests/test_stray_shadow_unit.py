"""Unit tests for the stray-shadow detector logic (HATS-791).

:func:`ai_hats.cli.maintenance.find_stray_launchers` is PURE — it takes a
``PATH`` string and the sanctioned launcher dest, so the truth table is
testable without mutating the process environment. NEVER deletes: the function
only reports.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ai_hats.cli.maintenance import find_stray_launchers


def _make_launcher(dir_: Path) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / "ai-hats"
    p.write_text("#!/usr/bin/env bash\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def test_sanctioned_launcher_not_flagged(tmp_path: Path):
    sanctioned_dir = tmp_path / "local" / "bin"
    sanctioned = _make_launcher(sanctioned_dir)
    path_env = str(sanctioned_dir)
    assert find_stray_launchers(path_env, sanctioned) == []


def test_stray_outside_sanctioned_is_flagged(tmp_path: Path):
    sanctioned_dir = tmp_path / "local" / "bin"
    sanctioned = _make_launcher(sanctioned_dir)
    stray_dir = tmp_path / "appvenv" / "bin"
    stray = _make_launcher(stray_dir)
    path_env = os.pathsep.join([str(sanctioned_dir), str(stray_dir)])

    strays = find_stray_launchers(path_env, sanctioned)
    assert strays == [stray]


def test_multiple_strays_in_path_order_deduped(tmp_path: Path):
    sanctioned_dir = tmp_path / "local" / "bin"
    sanctioned = _make_launcher(sanctioned_dir)
    s1 = _make_launcher(tmp_path / "venvA" / "bin")
    s2 = _make_launcher(tmp_path / "venvB" / "bin")
    # Sanctioned listed twice (deduped), strays in order.
    path_env = os.pathsep.join(
        [str(sanctioned_dir), str(s1.parent), str(s2.parent), str(sanctioned_dir)]
    )
    assert find_stray_launchers(path_env, sanctioned) == [s1, s2]


def test_non_executable_ai_hats_ignored(tmp_path: Path):
    sanctioned_dir = tmp_path / "local" / "bin"
    sanctioned = _make_launcher(sanctioned_dir)
    plain_dir = tmp_path / "data"
    plain_dir.mkdir()
    (plain_dir / "ai-hats").write_text("not executable")  # no +x
    path_env = os.pathsep.join([str(sanctioned_dir), str(plain_dir)])
    assert find_stray_launchers(path_env, sanctioned) == []


def test_empty_path_entries_skipped(tmp_path: Path):
    sanctioned_dir = tmp_path / "local" / "bin"
    sanctioned = _make_launcher(sanctioned_dir)
    # Leading/trailing empty entries (e.g. "::" or trailing ":") must not crash.
    path_env = os.pathsep.join(["", str(sanctioned_dir), ""])
    assert find_stray_launchers(path_env, sanctioned) == []
