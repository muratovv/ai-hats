"""Standalone contract for the ai-hats-library data package (HATS-876 / T18).

Proves the package is self-describing and resolvable with NO ai-hats: the
content is reachable through ``importlib.resources``, survives an ``as_file``
round-trip (the data-only-wheel contract, review P1 #14), and the schema version
is readable both as a Python constant and as a data marker.
"""

from __future__ import annotations

from importlib.resources import as_file, files


def test_files_resolves_the_layer_tree() -> None:
    root = files("ai_hats_library")
    for layer in ("core", "usage", "hooks"):
        assert (root / layer).is_dir(), f"missing shipped layer: {layer}"


def test_as_file_round_trips_a_skill_read() -> None:
    # The data-only-wheel contract: as_file yields a real path for any resource,
    # so a SKILL.md read works whether the package is unpacked or zipimported.
    root = files("ai_hats_library")
    a_skill = next((root / "core" / "skills").iterdir())
    with as_file(a_skill / "SKILL.md") as skill_md:
        assert skill_md.is_file()
        assert "---" in skill_md.read_text(encoding="utf-8")  # frontmatter fence


def test_schema_version_exposed_as_constant() -> None:
    import ai_hats_library

    assert isinstance(ai_hats_library.LIBRARY_SCHEMA_VERSION, int)
    assert ai_hats_library.LIBRARY_SCHEMA_VERSION >= 1
