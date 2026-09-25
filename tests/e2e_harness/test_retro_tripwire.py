"""The e2e tripwire's reading of retro.log: which lines mean a reviewer really started."""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from _helpers.retro_tripwire import reviewer_spawns
from ai_hats.retro.auto_retro import write_retro_log


def _log(tmp_path, sid: str, *lines: tuple[str, str, str]) -> None:
    layout = ProjectLayout.at(tmp_path)
    for source, action, detail in lines:
        write_retro_log(layout, sid, source, action, detail)


def test_a_reviewer_spawned_in_the_background_is_found(tmp_path):
    _log(
        tmp_path,
        "S1",
        ("runtime", "decision", "run: policy=always"),
        ("runtime", "outcome", "spawn-bg (HATS_SKIP_RETRO=None)"),
        ("session-reviewer", "spawn", "pid=4242 bg"),
    )

    found = reviewer_spawns(tmp_path)

    assert len(found) == 1 and "session-reviewer\tspawn\tpid=4242" in found[0], found
    assert "session_S1" in found[0], found


def test_a_reviewer_run_in_process_or_by_the_shell_hook_is_found(tmp_path):
    _log(tmp_path, "S2", ("runtime", "outcome", "sync-start (HATS_SKIP_RETRO=None)"))
    _log(tmp_path, "S3", ("hook", "spawn", "pid=7 bg"))

    assert len(reviewer_spawns(tmp_path)) == 2


def test_a_guarded_skipped_or_failed_session_is_not(tmp_path):
    _log(
        tmp_path,
        "S4",
        ("runtime", "decision", "run: policy=always"),
        ("runtime", "outcome", "suppressed-by-guard (HATS_SKIP_RETRO='1')"),
    )
    _log(tmp_path, "S5", ("runtime", "decision", "skip: below threshold (turns=2<5)"))
    _log(tmp_path, "S6", ("session-reviewer", "spawn-failed", "OSError()"))

    assert reviewer_spawns(tmp_path) == []
