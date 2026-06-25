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
            "runtime-hook markdown-format-post_md_format updated (content drift) + re-wired"
            in text
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
