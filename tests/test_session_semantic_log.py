"""HATS-948 (T15) — Session semantic tag methods write the right TraceTag.

RED-under-revert: mis-wiring log_sys→[SUB] (or dropping a method) fails here. These
methods are why runtime bricks write traces without importing observe's TraceTag.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats_observe import SessionManager
from ai_hats.paths import runs_dir


def _session(tmp_path: Path):
    return SessionManager(runs_dir=runs_dir(tmp_path)).create_session()


def test_semantic_methods_emit_expected_tags(tmp_path: Path) -> None:
    s = _session(tmp_path)
    s.log_sys("system line")
    s.log_sub("subagent line")
    s.log_res("response line")
    lines = s.trace_path.read_text().splitlines()
    assert any("[SYS] system line" in ln for ln in lines)
    assert any("[SUB] subagent line" in ln for ln in lines)
    assert any("[RES] response line" in ln for ln in lines)
