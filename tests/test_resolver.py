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

    core = Path(str(files("ai_hats_library") / "core"))
    resolver = LibraryResolver([core])

    for name in ("reflect-all", "reflect-role", "initial-wizard"):
        path = resolver.resolve_injection(name)
        assert path is not None, f"built-in injection {name!r} not found"
        assert path.read_text()  # non-empty


def test_list_components_symlinked_component(tmp_path: Path) -> None:
    """HATS-1505: list_components discovers components whose directory is a symlink."""
    from ai_hats.models import ComponentType

    lib = tmp_path / "lib"
    traits_dir = lib / "traits"
    traits_dir.mkdir(parents=True)

    # Regular component
    (traits_dir / "regular").mkdir()
    (traits_dir / "regular" / "config.yaml").write_text("name: regular\n")

    # Symlinked component
    external = tmp_path / "external_trait"
    external.mkdir()
    (external / "config.yaml").write_text("name: symlinked\n")
    (traits_dir / "symlinked").symlink_to(external, target_is_directory=True)

    resolver = LibraryResolver([lib])
    components = resolver.list_components(ComponentType.TRAIT)

    assert "regular" in components
    assert "symlinked" in components


def test_list_components_symlinked_namespace(tmp_path: Path) -> None:
    """HATS-1505: list_components discovers components under a symlinked namespace directory."""
    from ai_hats.models import ComponentType

    lib = tmp_path / "lib"
    traits_dir = lib / "traits"
    traits_dir.mkdir(parents=True)

    external_ns = tmp_path / "external_ns"
    (external_ns / "python").mkdir(parents=True)
    (external_ns / "python" / "config.yaml").write_text("name: dev::python\n")

    (traits_dir / "dev").symlink_to(external_ns, target_is_directory=True)

    resolver = LibraryResolver([lib])
    components = resolver.list_components(ComponentType.TRAIT)

    assert "dev::python" in components


def test_list_components_convergence_with_find_component_dir(tmp_path: Path) -> None:
    """HATS-1505: Every directory bearing a marker listed by list_components must be
    resolvable by find_component_dir, and vice versa."""
    from ai_hats.library_paths import find_component_dir
    from ai_hats.models import ComponentType

    lib = tmp_path / "lib"
    traits_dir = lib / "traits"
    traits_dir.mkdir(parents=True)

    # 1. Plain
    (traits_dir / "plain").mkdir()
    (traits_dir / "plain" / "config.yaml").write_text("name: plain\n")

    # 2. Symlinked component
    ext1 = tmp_path / "ext1"
    ext1.mkdir()
    (ext1 / "config.yaml").write_text("name: symlink_comp\n")
    (traits_dir / "symlink_comp").symlink_to(ext1, target_is_directory=True)

    # 3. Symlinked namespace
    ext_ns = tmp_path / "ext_ns"
    (ext_ns / "sub").mkdir(parents=True)
    (ext_ns / "sub" / "config.yaml").write_text("name: ns::sub\n")
    (traits_dir / "ns").symlink_to(ext_ns, target_is_directory=True)

    # 4. DAG aliases (two symlinks pointing to the same real directory)
    ext_shared = tmp_path / "ext_shared"
    ext_shared.mkdir()
    (ext_shared / "config.yaml").write_text("name: shared\n")
    (traits_dir / "alias_a").symlink_to(ext_shared, target_is_directory=True)
    (traits_dir / "alias_b").symlink_to(ext_shared, target_is_directory=True)

    resolver = LibraryResolver([lib])
    listed = set(resolver.list_components(ComponentType.TRAIT))

    expected_marker_components = {"plain", "symlink_comp", "ns::sub", "alias_a", "alias_b"}
    assert listed == expected_marker_components

    for comp in expected_marker_components:
        found = find_component_dir([lib], "traits", comp.replace("::", "/"))
        assert found is not None, f"find_component_dir failed for {comp!r}"


def test_list_components_symlink_cycle(tmp_path: Path) -> None:
    """HATS-1505: list_components terminates safely on directory symlink cycles."""
    from ai_hats.models import ComponentType

    lib = tmp_path / "lib"
    traits_dir = lib / "traits"
    traits_dir.mkdir(parents=True)

    (traits_dir / "valid").mkdir()
    (traits_dir / "valid" / "config.yaml").write_text("name: valid\n")

    # Symlink cycle: traits/loop points back to traits_dir
    (traits_dir / "loop").symlink_to(traits_dir, target_is_directory=True)

    resolver = LibraryResolver([lib])
    components = resolver.list_components(ComponentType.TRAIT)

    assert "valid" in components


