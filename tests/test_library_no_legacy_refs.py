"""Regression test for HATS-399: no legacy-path refs in bundled library content.

Why: ai-hats's ``library/`` tree is the source of truth for managed skills,
rules, hooks, and traits that get published to consumer projects on
``ai-hats self bump``. If any file in this tree references the legacy
layout (``.agent/{hooks,rules,skills,backlog,...}/``), the publish step
re-injects those stale paths into every consumer's ``.claude/skills/`` /
``.githooks/`` / etc. — making ``HATS-397`` healer's work
non-idempotent (round 2 of ``bump`` keeps re-fixing them).

This test walks ``library/`` and asserts the
``LEGACY_PATH_MAP``-derived regex matches nothing. New legacy refs added
to the bundled library will fail the suite.

If a legitimate historical mention is needed (e.g. a migration-themed
skill demonstrating the old → new layout), add the file path to
``ALLOWED_HISTORICAL_FILES`` with a one-line reason.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.migration_healer import _LEGACY_RE


# Files that may legitimately mention legacy paths as historical fact.
# Keep this list small and well-justified — the default expectation is
# zero matches.
ALLOWED_HISTORICAL_FILES: set[str] = set()


REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_DIR = REPO_ROOT / "library"


def _walk_library_files():
    """Yield every file under ``library/``, skipping VCS / cache noise."""
    skip_dir_names = {".git", "__pycache__", "node_modules"}
    stack: list[Path] = [LIBRARY_DIR]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in skip_dir_names:
                    continue
                stack.append(entry)
            elif entry.is_file():
                yield entry


def test_library_has_no_legacy_path_refs() -> None:
    """No file under ``library/`` may contain a legacy-path substring.

    See module docstring for rationale.
    """
    offenders: list[tuple[str, int, str]] = []
    for path in _walk_library_files():
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            rel = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = str(path)
        if rel in ALLOWED_HISTORICAL_FILES:
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            for match in _LEGACY_RE.finditer(line):
                offenders.append((rel, lineno, match.group(0)))

    if offenders:
        formatted = "\n".join(
            f"  {file}:{line} — '{substr}'"
            for file, line, substr in offenders
        )
        raise AssertionError(
            f"Legacy-path refs found in library/ ({len(offenders)} hit(s)):\n"
            f"{formatted}\n\n"
            "Bundled library/ is the source of truth that gets published to "
            "consumer projects on `ai-hats self bump`. Stale refs here cause "
            "HATS-397 healer to keep re-fixing the same published mirrors on "
            "every bump (non-idempotent). Rewrite the legacy substring to "
            "its `<ai-hats_dir>/...` equivalent, or — if the mention is a "
            "legitimate historical fact — add the file path to "
            "ALLOWED_HISTORICAL_FILES with a justification."
        )
