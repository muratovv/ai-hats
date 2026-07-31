"""HATS-833 — the startup-notice channel: structured note/warn rendering and the
session-start heal-note formatting (folded kinds, version-skew warn)."""

from ai_hats.hooks_manager import HookChange
from ai_hats.runtime_common import (
    StartupNotice,
    _print_startup_notices,
    _print_startup_warnings,
)
from ai_hats.wrap_runner import _format_hook_heal, _format_version_skew


class TestPrintStartupNotices:
    def test_notes_and_warns_render_in_separate_blocks(self, capsys):
        _print_startup_notices(
            [StartupNotice("note", "healed X"), StartupNotice("warn", "preload failed")]
        )
        out = capsys.readouterr().out
        assert "startup note(s)" in out and "healed X" in out
        assert "startup warning(s)" in out and "preload failed" in out
        # notes block printed before warns block
        assert out.index("note(s)") < out.index("warning(s)")

    def test_clean_emits_nothing(self, capsys):
        _print_startup_notices([])
        assert capsys.readouterr().out == ""

    def test_warnings_shim_uses_warn_path(self, capsys):
        _print_startup_warnings(["boom"])
        out = capsys.readouterr().out
        assert "startup warning(s)" in out and "boom" in out
        assert "note(s)" not in out

    def test_show_fatal_notice_and_exit_prints_stderr_and_exits(self, capsys):
        import pytest
        from ai_hats.startup_notices import show_fatal_notice_and_exit

        with pytest.raises(SystemExit) as exc_info:
            show_fatal_notice_and_exit("critical failure", exit_code=1)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Error:" in err and "critical failure" in err


class TestDiagnosticsPersistence:
    def test_non_interactive_env_precedence(self):
        from ai_hats.startup_notices import _startup_hold_seconds

        # AI_HATS_NON_INTERACTIVE=1 returns 0.0 even if is_tty=True and has_warnings=True
        res = _startup_hold_seconds(True, is_tty=True, env={"AI_HATS_NON_INTERACTIVE": "1"})
        assert res == 0.0

        # Precedence over AI_HATS_STARTUP_HOLD=10
        res = _startup_hold_seconds(
            True,
            is_tty=True,
            env={"AI_HATS_NON_INTERACTIVE": "1", "AI_HATS_STARTUP_HOLD": "10"},
        )
        assert res == 0.0

    def test_strip_ansi_and_control_codes(self):
        from ai_hats.startup_notices import strip_ansi_and_control_codes

        raw = "\033[1;32m✨ Clean start!\033[0m\r\033[J\033[=0;1u"
        clean = strip_ansi_and_control_codes(raw)
        assert clean == "✨ Clean start!"

    def test_save_session_diagnostics_atomic_and_sequential(self, tmp_path):
        import json
        from ai_hats.startup_notices import save_session_diagnostics

        s_dir = tmp_path / "session_test"
        s_dir.mkdir()

        # Step 1: startup
        save_session_diagnostics(s_dir, "startup", {"hold_seconds": 0.0, "notices": []})
        diag = json.loads((s_dir / "diagnostics.json").read_text())
        assert diag["schema_version"] == 1
        assert diag["startup"] == {"hold_seconds": 0.0, "notices": []}

        # Step 2: completion
        save_session_diagnostics(s_dir, "completion", {"duration": "10s", "req_count": 2})
        diag = json.loads((s_dir / "diagnostics.json").read_text())
        assert diag["startup"] == {"hold_seconds": 0.0, "notices": []}
        assert diag["completion"] == {"duration": "10s", "req_count": 2}

        # Temp file created inside session_dir (no EXDEV)
        assert not list(s_dir.glob("diag_*.tmp"))

    def test_save_session_diagnostics_corrupted_backup(self, tmp_path):
        import json
        from ai_hats.startup_notices import save_session_diagnostics

        s_dir = tmp_path / "session_corrupt"
        s_dir.mkdir()

        # Write corrupted JSON
        (s_dir / "diagnostics.json").write_text("corrupted json {")
        save_session_diagnostics(s_dir, "startup", {"hold_seconds": 0.0})

        assert (s_dir / "diagnostics.json.corrupted").exists()
        diag = json.loads((s_dir / "diagnostics.json").read_text())
        assert diag["startup"] == {"hold_seconds": 0.0}

    def test_save_session_diagnostics_fail_soft_on_missing_dir(self):
        from ai_hats.startup_notices import save_session_diagnostics

        # None or non-existent path should not raise
        save_session_diagnostics(None, "startup", {})
        save_session_diagnostics("/nonexistent/path/session_123", "startup", {})

    def test_show_fatal_notice_and_exit_saves_diagnostics(self, tmp_path, capsys):
        import json
        import pytest
        from ai_hats.startup_notices import show_fatal_notice_and_exit

        s_dir = tmp_path / "session_fatal"
        s_dir.mkdir()

        with pytest.raises(SystemExit) as exc_info:
            show_fatal_notice_and_exit("role foo not found", exit_code=1, session_dir=s_dir)

        assert exc_info.value.code == 1
        diag = json.loads((s_dir / "diagnostics.json").read_text())
        assert diag["startup"]["notices"][0]["level"] == "fatal"
        assert "role foo not found" in diag["startup"]["notices"][0]["text"]



class TestFormatHookHeal:
    def test_groups_by_surface_and_names_kinds(self):
        text = _format_hook_heal(
            [
                HookChange("wt", "hunk-review-comments-drain-review.sh", "missing"),
                HookChange("git", "pre-push", "content"),
            ]
        )
        assert text.startswith("managed hooks healed at start — ")
        assert "wt-hook hunk-review-comments-drain-review materialized (was missing)" in text
        assert "git-hook pre-push updated (content drift)" in text

    def test_folds_multiple_kinds_on_one_hook(self):
        text = _format_hook_heal(
            [
                HookChange("runtime", "markdown-format-post_md_format.py", "content"),
                HookChange("runtime", "markdown-format-post_md_format.py", "wiring"),
            ]
        )
        # one clause, both kinds folded; extension stripped for display
        assert (
            "runtime-hook markdown-format-post_md_format updated (content drift) + re-wired" in text
        )
        assert text.count("markdown-format-post_md_format") == 1


class TestFormatVersionSkew:
    def test_names_unhealed_drift_and_points_at_update(self):
        text = _format_version_skew(
            [HookChange("runtime", "markdown-format-post_md_format.py", "content")]
        )
        assert "behind upstream" in text
        assert "ai-hats self update" in text
        assert "runtime markdown-format-post_md_format" in text
