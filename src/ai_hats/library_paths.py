"""The ordered library roots a resolver searches (HATS-1269 S1).

Lifted out of ``Assembler._build_library_paths`` so worktree teardown can build
the same list without a full ``Assembler`` — whose ``__init__`` loads
``ai-hats.yaml``, validates the provider and checks the library schema, any of
which raising inside a fail-closed teardown would block a merge for an unrelated
reason. One builder, two callers: a second ordering would drift, resolving a
skill at teardown that composition never saw. The built-in schema check stays
with the assembler — failing loud belongs on ``init``/``bump``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .paths import builtin_library_layers, user_home
from .paths.constants import LIBRARIES_DIRNAME


LIBRARY_PATHS_CONFIG = "library_paths.yaml"


def _user_global_library_paths() -> list[Path]:
    """User-global library layers configured in ``~/.ai-hats/library_paths.yaml``.

    Format: ``paths: [<dir>, ...]``. Returns valid directory paths. Invalid or
    non-existent entries, malformed YAML, or unreadable files log a warning and
    are skipped so an invalid file cannot break composition or teardown.
    """
    import logging
    import yaml

    config_file = user_home() / ".ai-hats" / LIBRARY_PATHS_CONFIG
    if not config_file.is_file():
        return []

    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "user library paths: failed to load %s (%s)", config_file, exc
        )
        return []

    if not isinstance(data, dict):
        logging.getLogger(__name__).warning(
            "user library paths: expected dict in %s, got %s", config_file, type(data).__name__
        )
        return []

    raw_paths = data.get("paths")
    if not isinstance(raw_paths, list):
        if raw_paths is not None:
            logging.getLogger(__name__).warning(
                "user library paths: expected list for 'paths' in %s, got %s",
                config_file,
                type(raw_paths).__name__,
            )
        return []

    result: list[Path] = []
    for entry in raw_paths:
        if not isinstance(entry, (str, Path)):
            logging.getLogger(__name__).warning(
                "user library paths: invalid entry in %s: %r", config_file, entry
            )
            continue
        p = Path(entry).expanduser()
        if not p.is_dir():
            logging.getLogger(__name__).warning(
                "user library paths: directory does not exist: %s (from %s)", p, config_file
            )
            continue
        result.append(p)
    return result


def build_library_paths(
    project_dir: Path,
    *,
    config_paths: Sequence[str | Path] = (),
    local_libraries: Path | None = None,
    extra: Sequence[Path] = (),
) -> list[Path]:
    """Ordered library roots, earlier = lower priority (``LibraryResolver`` last-wins).

    Built-in shipping (``core`` then ``usage``) first; override points —
    entry-point packages, user-global (``~/.ai-hats/`` and ``~/.ai-hats/library_paths.yaml``),
    config-specified, project-local, explicit ``extra`` — layer on top. ``local_libraries``
    overrides the project-local layer (the worktree re-point, HATS-831); ``None`` means
    ``<project_dir>/libraries``.
    """
    paths: list[Path] = list(builtin_library_layers(project_dir))

    # HATS-871 / ADR-0016: out-of-tree packages contribute their skills/ via the
    # ``ai_hats.skills`` entry-point (open registry). Shipped tier — ranks above
    # the builtins, below the user/config/project overrides that follow.
    from .skill_sources import skill_source_roots

    paths.extend(skill_source_roots())

    # ``user_home()`` honours ``AI_HATS_USER_HOME`` (HATS-532) for e2e isolation.
    global_lib = user_home() / ".ai-hats"
    if global_lib.is_dir():
        paths.append(global_lib)

    paths.extend(_user_global_library_paths())

    for configured in config_paths:
        expanded = Path(configured).expanduser()
        if expanded.is_dir():
            paths.append(expanded)

    local_lib = local_libraries or project_dir / LIBRARIES_DIRNAME
    if local_lib.is_dir():
        paths.append(local_lib)

    paths.extend(extra)
    return paths


def find_component_dir(roots: Sequence[Path], subdir: str, name: str) -> Path | None:
    """Last-wins search for ``<root>/<subdir>/<name>`` across ordered ``roots``.

    The path half of component resolution, kept out of ``resolver`` so a brick
    can reach it without importing the composition layer (ADR-0014 / HATS-865);
    ``LibraryResolver.resolve`` delegates here, so there is one search, not two.
    Namespace notation (``dev::python``) is already mapped to a subpath by the
    caller.
    """
    found: Path | None = None
    for root in roots:
        candidate = root / subdir / name
        if candidate.is_dir():
            found = candidate
    return found
