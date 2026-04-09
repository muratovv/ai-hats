"""Schema migration registry — independent chains per schema family.

Each schema family (`hats-session-retro`, `hats-bundle`, `hats-judge-retro`)
has its own LATEST version and its own migration chain. Migrations operate
on raw dicts (parsed YAML frontmatter), not on pydantic models, so they
remain valid even after old model classes are deleted from the codebase.

Per design decision D6: only the LATEST pydantic class for each family
lives in the source tree. Old retro files are migrated on load via
`migrate_to_latest(data)` and then validated against the LATEST model.
"""

from __future__ import annotations

from collections.abc import Callable

from .aggregation import SCHEMA_VERSION as AGGREGATION_LATEST
from .bundle import SCHEMA_VERSION as BUNDLE_LATEST
from .judge_retro import SCHEMA_VERSION as JUDGE_RETRO_LATEST
from .session_retro import SCHEMA_VERSION as SESSION_RETRO_LATEST

LATEST_VERSIONS: dict[str, str] = {
    "hats-session-retro": SESSION_RETRO_LATEST,
    "hats-bundle": BUNDLE_LATEST,
    "hats-judge-retro": JUDGE_RETRO_LATEST,
    "hats-aggregation": AGGREGATION_LATEST,
}

#: registry of migration steps; key is (from_version, to_version)
MIGRATIONS: dict[tuple[str, str], Callable[[dict], dict]] = {}


def family_of(version: str) -> str:
    """Extract family prefix from a `family/vN` schema version string."""
    if "/" not in version:
        raise ValueError(
            f"Invalid schema version format: {version!r} (expected 'family/vN')"
        )
    return version.rsplit("/", 1)[0]


def register(
    from_version: str, to_version: str
) -> Callable[[Callable[[dict], dict]], Callable[[dict], dict]]:
    """Decorator to register a migration step.

    The migration function receives the raw dict and must return a new dict
    with the `schema` field set to `to_version`.
    """

    def deco(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        key = (from_version, to_version)
        if key in MIGRATIONS:
            raise ValueError(f"Duplicate migration: {from_version} → {to_version}")
        if family_of(from_version) != family_of(to_version):
            raise ValueError(
                f"Cross-family migration not allowed: "
                f"{from_version} ({family_of(from_version)}) → "
                f"{to_version} ({family_of(to_version)})"
            )
        MIGRATIONS[key] = fn
        return fn

    return deco


def migrate_to_latest(data: dict) -> dict:
    """Walk migration chain from data['schema'] to LATEST for its family.

    Detects cycles, missing chain steps, and migrations that fail to
    update the schema field. Returns a new dict with the latest schema.
    """
    current = data.get("schema")
    if current is None:
        raise ValueError("Frontmatter missing required 'schema' field")
    if not isinstance(current, str):
        raise ValueError(f"Schema field must be a string, got {type(current).__name__}")

    family = family_of(current)
    target = LATEST_VERSIONS.get(family)
    if target is None:
        known = ", ".join(sorted(LATEST_VERSIONS))
        raise ValueError(
            f"Unknown schema family: {family!r}. Known families: {known}"
        )

    visited: set[str] = {current}
    while current != target:
        next_step = next(
            ((f, t) for (f, t) in MIGRATIONS if f == current), None
        )
        if next_step is None:
            raise ValueError(
                f"No migration path from {current!r} to {target!r}. "
                f"Register a migration via @register({current!r}, ...)"
            )
        data = MIGRATIONS[next_step](dict(data))
        new_current = data.get("schema")
        if new_current is None or not isinstance(new_current, str):
            raise ValueError(
                f"Migration {next_step[0]} → {next_step[1]} did not set "
                f"a valid 'schema' field on output"
            )
        if new_current in visited:
            raise ValueError(
                f"Migration cycle detected at {new_current!r} "
                f"(visited: {sorted(visited)})"
            )
        visited.add(new_current)
        current = new_current
    return data
