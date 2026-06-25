"""HATS-831 single-home guard — pin builtin-library resolution to paths.py.

``importlib.resources.files("ai_hats.library")`` hard-pins the editable install
to the MAIN repo regardless of cwd, so ANY direct call outside the single
resolver re-introduces the worktree-invisibility bug class (HATS-826 fixed one
instance; HATS-831 unified the rest). This guard asserts the call appears ONLY
in ``src/ai_hats/paths.py`` (the resolver), with one justified whitelist
exception (``cli/maintenance.py`` — a diagnostic that intentionally reports the
INSTALLED package path).

Mirrors ``tests/test_no_direct_compose_outside_facade.py``. AST-based, so it is
immune to docstring / comment / string-literal false positives.
"""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src" / "ai_hats"

# The sole home for builtin-library SOURCE resolution.
RESOLVER_FILE = SRC_DIR / "paths.py"

# Whitelisted exceptions: file -> justification (non-empty, enforced below).
ALLOWED_FILES: dict[Path, str] = {
    SRC_DIR / "cli" / "maintenance.py": (
        "_library_path() is a diagnostic that reports where the package is "
        "INSTALLED ('installed @ <path>') for `ai-hats doctor/info`; the "
        "importlib path is the correct answer there, not a compose-time read."
    ),
}


def _find_library_files_calls(text: str) -> list[tuple[int, str]]:
    """AST detector for ``files("ai_hats.library")`` calls (any spelling).

    Matches a ``Call`` whose ``func`` is ``files`` — a bare ``Name`` or any
    ``Attribute`` ending in ``.files`` (e.g. ``importlib.resources.files``) —
    and whose first positional arg is the constant ``"ai_hats.library"``.
    Returns ``[(lineno, source_segment)]``; ``[]`` for unparseable files
    (other tests catch syntax errors).
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            fname = func.id
        elif isinstance(func, ast.Attribute):
            fname = func.attr
        else:
            continue
        if fname != "files" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value == "ai_hats.library":
            try:
                segment = ast.unparse(node)
            except Exception:  # noqa: BLE001 — ast.unparse is reliable on 3.11+
                segment = 'files("ai_hats.library")'
            found.append((node.lineno, segment))
    return found


def test_builtin_library_files_call_only_in_resolver():
    """No ``files("ai_hats.library")`` call may live outside ``paths.py``
    (+ the whitelisted diagnostic). Any other hit re-introduces the
    MAIN-pinned, worktree-invisible resolution the refactor eliminated.
    """
    offenders: list[tuple[Path, int, str]] = []
    for py_file in SRC_DIR.rglob("*.py"):
        if py_file == RESOLVER_FILE or py_file in ALLOWED_FILES:
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, segment in _find_library_files_calls(text):
            offenders.append((py_file.relative_to(REPO_ROOT), lineno, segment))

    assert not offenders, (
        'HATS-831 drift: files("ai_hats.library") called outside '
        "src/ai_hats/paths.py. importlib hard-pins the MAIN repo regardless of "
        "cwd, so this re-introduces the worktree-invisibility bug. Route through "
        "paths.builtin_library_root / builtin_library_layers / "
        "builtin_library_hooks / core_pipeline_path instead.\n"
        + "\n".join(f"  {p}:{ln}: {seg}" for p, ln, seg in offenders)
    )


def test_resolver_actually_contains_the_call():
    """Sanity: the resolver IS the single home — it must contain the call, else
    the guard passes vacuously after an accidental move/rename.
    """
    text = RESOLVER_FILE.read_text(encoding="utf-8")
    assert _find_library_files_calls(text), (
        'expected files("ai_hats.library") in paths.py (the resolver); '
        "if it moved, update RESOLVER_FILE."
    )


def test_detector_flags_a_synthetic_hit():
    """Green must mean 'no stray site', not 'detector broken' — fire on a
    synthetic positive, and stay silent on prose + unrelated ``files()`` calls.
    """
    positive = 'from importlib.resources import files\nx = files("ai_hats.library") / "hooks"\n'
    assert len(_find_library_files_calls(positive)) == 1

    qualified = 'import importlib.resources as ir\nir.files("ai_hats.library")\n'
    assert len(_find_library_files_calls(qualified)) == 1

    negative = '"""prose mentioning the literal here"""\nfiles("some.other.pkg")\n'
    assert _find_library_files_calls(negative) == []


def test_whitelist_entries_have_justifications():
    """Keep the whitelist from drifting into a silent escape hatch."""
    for path, justification in ALLOWED_FILES.items():
        assert justification.strip(), (
            f"Whitelist entry {path} needs a justification explaining why a "
            'direct files("ai_hats.library") call is legitimate there.'
        )
