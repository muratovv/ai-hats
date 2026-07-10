"""Library format-schema compatibility guard (HATS-876 / T18, ADR-0014 §5).

The versioned seam between ai-hats and the library is the FORMAT SCHEMA, not a
Python API: each library release declares a ``schema_version`` (data marker at
its root, ``manifest.yaml``); ai-hats declares the max it understands and fails
loud when the resolved built-in library is newer — the analog of the
``ai-hats.yaml`` ``KNOWN_SCHEMA_VERSION`` fail-loud-when-newer. This gates ONLY
the built-in pinned package; user overlays (``~/.ai-hats`` / ``library_paths``)
are the user's own content and are never checked here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Highest library format-schema version this ai-hats binary understands. Bump in
# lockstep with a breaking change to skill frontmatter / composition schema /
# resolver expectations (a library MAJOR).
SUPPORTED_LIBRARY_SCHEMA = 1

LIBRARY_MANIFEST = "manifest.yaml"


class LibrarySchemaError(Exception):
    """Raised when the resolved library declares a schema newer than supported."""


def read_library_schema_version(root: Path) -> int:
    """The library's declared ``schema_version``; ``1`` when absent/unversioned.

    Read from the resolved root's ``manifest.yaml`` so it works uniformly for an
    installed package, an ``AI_HATS_LIBRARY_ROOT`` dir, or a source checkout. An
    absent/malformed manifest is treated as the baseline (legacy content is
    schema 1), never a hard error — the guard only fires on a *too-new* library.
    """
    manifest = root / LIBRARY_MANIFEST
    if not manifest.is_file():
        return 1
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        return int(data.get("schema_version", 1))
    except (yaml.YAMLError, OSError, TypeError, ValueError):
        return 1


def check_library_schema(root: Path | None) -> None:
    """Fail loud if the built-in library at ``root`` is newer than supported.

    ``None`` (broken install) is a no-op — a separate concern from a version
    mismatch. The error names ``ai-hats self update`` as the remedy.
    """
    if root is None:
        return
    found = read_library_schema_version(root)
    if found > SUPPORTED_LIBRARY_SCHEMA:
        raise LibrarySchemaError(
            f"library format-schema v{found} at {root} is newer than this ai-hats "
            f"understands (supports <= v{SUPPORTED_LIBRARY_SCHEMA}). "
            f"Run `ai-hats self update` to get an ai-hats that speaks it."
        )


__all__ = [
    "SUPPORTED_LIBRARY_SCHEMA",
    "LibrarySchemaError",
    "read_library_schema_version",
    "check_library_schema",
]
