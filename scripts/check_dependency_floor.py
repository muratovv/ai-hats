#!/usr/bin/env python3
"""A pin on a workspace package may not float below that package's version.

HATS-1399. The producer side is guarded (`check_pkg_version_skew.py` refuses a
`src/**` change whose diff carries no version bump); the consumer side was not. The root pyproject held `ai-hats-observe>=0.3.0` while the integrator
imported `is_measured`, added in 0.5.0 — so `self update` resolving from PyPI
could serve 0.3.0 and every `ai-hats` command would die on ImportError. No test
saw it: the work tree runs an editable workspace install where the floor never
participates in resolution at all.

The invariant is deliberately blunt — floor >= the package's current version.
A package is bumped exactly when its source changes, so "the floor fell behind
the version" is the shape that lets a stale wheel satisfy a fresh import.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Violation:
    consumer: str  # pyproject path, repo-relative
    package: str
    floor: Version
    version: Version

    def __str__(self) -> str:
        return (
            f"{self.consumer}: {self.package}>={self.floor} floats below the "
            f"package's own {self.version} — a resolver may serve a wheel that "
            f"predates the symbols this code imports. Raise the floor to "
            f">={self.version}."
        )


def declared_floor(spec: str) -> Version | None:
    """The lower bound a requirement string pins, or None when it pins none."""
    req = Requirement(spec)
    bounds = [
        Version(s.version.rstrip(".*")) for s in req.specifier if s.operator in (">=", "==", "~=")
    ]
    return max(bounds) if bounds else None


def requirement_strings(pyproject: dict) -> list[str]:
    """Every dependency string in a parsed pyproject, required and optional."""
    project = pyproject.get("project", {})
    specs = list(project.get("dependencies", []))
    for group in (project.get("optional-dependencies") or {}).values():
        specs.extend(group)
    return specs


def workspace_versions(root: Path) -> dict[str, Version]:
    """name -> version for every package carrying a static version under packages/."""
    found = {}
    for pyproject in sorted((root / "packages").rglob("pyproject.toml")):
        data = tomllib.loads(pyproject.read_text())
        project = data.get("project", {})
        name, version = project.get("name"), project.get("version")
        if name and version:
            found[name] = Version(version)
    return found


def violations(consumers: dict[str, dict], versions: dict[str, Version]) -> list[Violation]:
    """Pins on workspace packages whose floor is below the package's version."""
    out = []
    for consumer, data in sorted(consumers.items()):
        for spec in requirement_strings(data):
            name = Requirement(spec).name
            if name not in versions:
                continue
            floor = declared_floor(spec)
            if floor is not None and floor < versions[name]:
                out.append(Violation(consumer, name, floor, versions[name]))
    return out


def collect_consumers(root: Path) -> dict[str, dict]:
    paths = [root / "pyproject.toml", *sorted((root / "packages").rglob("pyproject.toml"))]
    return {str(p.relative_to(root)): tomllib.loads(p.read_text()) for p in paths if p.is_file()}


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]).resolve() if argv else REPO_ROOT
    versions = workspace_versions(root)
    found = violations(collect_consumers(root), versions)
    for v in found:
        print(f"[dependency-floor] FAIL: {v}", file=sys.stderr)
    if not found:
        print(
            f"[dependency-floor] ok: every pin on {len(versions)} workspace packages "
            "is at or above its version",
            file=sys.stderr,
        )
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
