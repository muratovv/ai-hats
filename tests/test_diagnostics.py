"""HATS-1753: the diagnostics primitive — one value type, one spelling."""

from __future__ import annotations

from pathlib import Path

from ai_hats.diagnostics import Diagnostic, Level, emit_to_stderr


def test_bare_diagnostic_renders_its_text_alone():
    """Given no source and no remedy, when rendered, then only the text."""
    diag = Diagnostic(Level.WARN, "the gate will NOT fire")

    assert diag.render() == "the gate will NOT fire"


def test_source_path_prefixes_the_text():
    """Given a source file, when rendered, then it names the file to open."""
    diag = Diagnostic(Level.WARN, "no integration collects 'foo'", where=Path("/lib/role.yaml"))

    assert diag.render() == "/lib/role.yaml: no integration collects 'foo'"


def test_remedy_lands_on_its_own_indented_line():
    """Given a remedy, when rendered, then it is an indented line of its own.

    The banner prints one notice as `  • {text}` and indents no continuation,
    so a producer that wants alignment indents its own — same as
    `_broken_hook_refs_text` and `_format_skill_collisions` already do.
    """
    diag = Diagnostic(Level.WARN, "dev env outdated", remedy="uv sync --inexact")

    assert diag.render() == "dev env outdated\n    uv sync --inexact"


def test_stderr_channel_marks_the_level_and_is_the_only_speller(capsys):
    """Given mixed levels, when emitted, then one spelling per level, all on stderr."""
    emit_to_stderr(
        [
            Diagnostic(Level.WARN, "gate disarmed"),
            Diagnostic(Level.NOTE, "mirror healed"),
        ]
    )

    captured = capsys.readouterr()
    assert captured.err == "WARN: gate disarmed\nNOTE: mirror healed\n"
    assert captured.out == ""
