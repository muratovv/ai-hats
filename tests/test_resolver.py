"""Tests for ``LibraryResolver`` — focus on ``resolve_injection`` (HATS-445).

Resolution of components (rules/skills/traits/roles) is covered indirectly
by ``tests/test_composer.py``. This file adds direct coverage for the new
sibling lookup over ``initial_injections/<name>.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.resolver import LibraryResolver


@pytest.fixture
def make_lib(tmp_path):
    """Factory that creates a library root with optional initial_injections."""

    def _make(name: str, injections: dict[str, str] | None = None) -> Path:
        lib = tmp_path / name
        lib.mkdir()
        if injections:
            inj_dir = lib / "initial_injections"
            inj_dir.mkdir()
            for fname, body in injections.items():
                (inj_dir / f"{fname}.md").write_text(body)
        return lib

    return _make


def test_resolve_injection_missing_returns_none(make_lib) -> None:
    lib = make_lib("only-lib")
    resolver = LibraryResolver([lib])
    assert resolver.resolve_injection("does-not-exist") is None


def test_resolve_injection_finds_in_single_library(make_lib) -> None:
    lib = make_lib("only-lib", {"probe": "PROBE_BODY"})
    resolver = LibraryResolver([lib])
    path = resolver.resolve_injection("probe")
    assert path is not None
    assert path == lib / "initial_injections" / "probe.md"
    assert path.read_text() == "PROBE_BODY"


def test_resolve_injection_last_wins_across_libraries(make_lib) -> None:
    """A later library_path overrides an earlier one (HATS-445).

    Mirrors the ``LibraryResolver.resolve`` semantic for components:
    built-in (first) → usage → ~/.ai-hats → cfg.library_paths →
    <project>/libraries (last). Last-wins is the user-facing override
    contract.
    """
    builtin = make_lib("builtin", {"reflect-all": "BUILTIN_TEXT"})
    project = make_lib("project", {"reflect-all": "PROJECT_OVERRIDE"})
    resolver = LibraryResolver([builtin, project])
    path = resolver.resolve_injection("reflect-all")
    assert path is not None
    assert path.read_text() == "PROJECT_OVERRIDE"


def test_resolve_injection_falls_back_when_only_earlier_has_it(make_lib) -> None:
    """If only the earlier library has the file, that one is returned."""
    builtin = make_lib("builtin", {"only-here": "BUILTIN_ONLY"})
    project = make_lib("project")  # no injections at all
    resolver = LibraryResolver([builtin, project])
    path = resolver.resolve_injection("only-here")
    assert path is not None
    assert path.read_text() == "BUILTIN_ONLY"


def test_resolve_injection_ignores_non_md_siblings(make_lib, tmp_path) -> None:
    """A ``.txt`` or extensionless file with the same stem is not picked up.

    Lookup is keyed by ``<name>.md`` strictly — no ambiguity, no fallback
    to other extensions.
    """
    lib = make_lib("lib")
    inj_dir = lib / "initial_injections"
    inj_dir.mkdir()
    (inj_dir / "probe.txt").write_text("WRONG_EXT")
    (inj_dir / "probe").write_text("NO_EXT")
    resolver = LibraryResolver([lib])
    assert resolver.resolve_injection("probe") is None


def test_resolve_injection_ignores_directory_with_matching_name(make_lib) -> None:
    """A directory named ``<name>.md`` is not a valid injection file."""
    lib = make_lib("lib")
    inj_dir = lib / "initial_injections"
    inj_dir.mkdir()
    (inj_dir / "probe.md").mkdir()  # directory, not a file
    resolver = LibraryResolver([lib])
    assert resolver.resolve_injection("probe") is None


def test_resolve_injection_empty_library_paths_returns_none() -> None:
    """No library paths → nothing to search → ``None``."""
    resolver = LibraryResolver([])
    assert resolver.resolve_injection("anything") is None


def test_resolve_injection_with_builtin_core(tmp_path) -> None:
    """Sanity check against the shipped built-in core library.

    Built-in injections (``reflect-all``, ``reflect-role``,
    ``initial-wizard``) must be resolvable when the built-in core path is
    in the chain — this is the dogfood proof that ``cli/reflect.py`` keeps
    working after the call-site migration.
    """
    from importlib.resources import files

    core = Path(str(files("ai_hats.library") / "core"))
    resolver = LibraryResolver([core])

    for name in ("reflect-all", "reflect-role", "initial-wizard"):
        path = resolver.resolve_injection(name)
        assert path is not None, f"built-in injection {name!r} not found"
        assert path.read_text()  # non-empty
