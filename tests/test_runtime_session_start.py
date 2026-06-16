"""Tests for ``_print_session_start`` (HATS-777).

The HITL session-start banner appends the running ai-hats version + install
channel to the tail of the banner line. Only the git-hash local segment of a
dev version (the part after ``+``) is colorized; the version base and the
``(channel)`` suffix stay plain. A clean release (no ``+``) is fully plain.
"""

from __future__ import annotations

from ai_hats.runtime_common import _print_session_start

_CYAN = "\033[36m"
_RESET = "\033[0m"


def test_dev_version_colorizes_only_the_hash(capsys):
    _print_session_start(
        "maintainer",
        "claude",
        "20260616-1",
        version="0.8.1.dev127+gf7f916378",
        channel="local",
    )
    out = capsys.readouterr().out
    assert f"| ai-hats v0.8.1.dev127+{_CYAN}gf7f916378{_RESET} (local)" in out


def test_clean_release_has_no_color_in_version(capsys):
    _print_session_start(
        "maintainer",
        "claude",
        "20260616-1",
        version="0.8.1",
        channel="stable",
    )
    out = capsys.readouterr().out
    assert "| ai-hats v0.8.1 (stable)" in out
    assert f"v0.8.1{_CYAN}" not in out  # nothing to highlight on a clean release


def test_no_version_keeps_legacy_banner(capsys):
    _print_session_start("maintainer", "claude", "20260616-1")
    out = capsys.readouterr().out
    assert "ai-hats v" not in out
    assert "[*] Role: " in out  # legacy banner still intact
