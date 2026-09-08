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


def user_global_library_paths() -> list[Path]:
    """All user-global library roots: ``~/.ai-hats`` (if dir) + ``library_paths.yaml``."""
    roots: list[Path] = []
    global_lib = user_home() / ".ai-hats"
    if global_lib.is_dir():
        roots.append(global_lib)
    roots.extend(_user_global_library_paths())
    return roots


def worktree_local_libraries(project_dir: Path) -> Path | None:
    """Project-local ``libraries/`` re-pointed to the linked worktree, or ``None``.

    Inside a linked worktree ``project_dir`` hopped to MAIN (HATS-524), so the
    git-tracked ``libraries/`` would resolve to MAIN — invisible to worktree
    edits. Re-point only when cwd is in a worktree whose main checkout IS
    ``project_dir``. The ``is_relative_to`` pre-gate skips the git probe on the
    common main-checkout path (and under subprocess-mocking tests).

    Lives beside :func:`build_library_paths` because every caller of that one
    owes this: a caller that skips it searches a root set the composition does
    not have, and a declaration it cannot see reads as absent (HATS-1141).
    """  # comment-length: allow — the second paragraph IS the reason it moved here
    cwd = Path.cwd()
    try:
        if cwd.resolve().is_relative_to(project_dir.resolve()):
            return None
    except (OSError, ValueError):
        return None

    from ai_hats_wt import WorktreeManager

    main_root = WorktreeManager.main_worktree_root(cwd)
    if main_root is None or main_root.resolve() != project_dir.resolve():
        return None
    wt_top = WorktreeManager.worktree_toplevel(cwd)
    return (wt_top / LIBRARIES_DIRNAME) if wt_top is not None else None


def build_library_paths(
    project_dir: Path,
    *,
    config_paths: Sequence[str | Path] = (),
    local_libraries: Path | None = None,
    extra: Sequence[Path] = (),
    prefer_cwd: bool = False,
    cwd: Path | None = None,
) -> list[Path]:
    """Build the ordered list of library root paths for component resolution.

    Order, lowest priority first — resolution is LAST-wins throughout
    (:func:`find_component_dir`), so a later root overrides an earlier one:
    1. Built-in skill source packages (e.g. ``ai-hats-library``)
    2. User global library (``~/.ai-hats`` + ``library_paths.yaml``)
    3. Project configured library paths (from ``ai-hats.yaml``)
    4. Project local libraries (``./libraries`` or explicit)
    5. Extra runtime overrides

    ``prefer_cwd`` is for READ-ONLY composition only, and ``cwd`` names the
    directory that counts as "here" — see
    :func:`ai_hats.paths.library.builtin_library_root`.
    """
    paths: list[Path] = list(builtin_library_layers(project_dir, prefer_cwd=prefer_cwd, cwd=cwd))

    # HATS-871 / ADR-0016: out-of-tree packages contribute their skills/ via the
    # ``ai_hats.skills`` entry-point (open registry). Shipped tier — ranks above
    # the builtins, below the user/config/project overrides that follow.
    from .skill_sources import skill_source_roots

    paths.extend(skill_source_roots())

    paths.extend(user_global_library_paths())

    for configured in config_paths:
        p = Path(configured).expanduser()
        expanded = (project_dir / p).resolve() if not p.is_absolute() else p.resolve()
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
