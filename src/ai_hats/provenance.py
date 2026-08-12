"""Component provenance classification and layer enum (HATS-525)."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from pathlib import Path


class ComponentLayer(str, Enum):
    """Source layer of a component in the dependency tree."""

    BUILT_IN = "built-in"
    GLOBAL = "global"
    PROJECT = "project"
    RUNTIME = "runtime"


def _try_get_global_layer(
    raw_path: Path | None,
    resolved: Path,
    global_roots: Sequence[Path] | None = None,
) -> ComponentLayer | None:
    """Probe if raw or resolved path belongs to any user-global library root."""
    if global_roots is None:
        from .library_paths import user_global_library_paths

        global_roots = user_global_library_paths()

    for root in global_roots:
        try:
            root_resolved = root.resolve()
        except (OSError, ValueError, TypeError):
            root_resolved = root

        if raw_path is not None:
            try:
                if raw_path.is_relative_to(root) or raw_path.is_relative_to(root_resolved):
                    return ComponentLayer.GLOBAL
            except (ValueError, TypeError):
                pass

        try:
            if resolved.is_relative_to(root) or resolved.is_relative_to(root_resolved):
                return ComponentLayer.GLOBAL
        except (ValueError, TypeError):
            pass

    return None


def _try_get_project_layer(
    resolved: Path,
    project_dir: Path,
    library_paths: list[Path | tuple],
    project_config_paths: list[str],
) -> ComponentLayer | None:
    """Probe if path belongs to project-local or project-configured library."""
    proj_root = project_dir.resolve()

    for lib in library_paths:
        lib_p = (
            Path(lib[0])
            if isinstance(lib, tuple)
            else (Path(lib) if isinstance(lib, (str, Path)) else None)
        )
        if lib_p is None:
            continue
        try:
            lib_resolved = lib_p.resolve()
        except (OSError, ValueError, TypeError):
            continue

        if resolved.is_relative_to(lib_resolved):
            if lib_resolved.is_relative_to(proj_root):
                return ComponentLayer.PROJECT
            for proj_p in project_config_paths:
                try:
                    p = Path(proj_p).expanduser()
                    proj_resolved = (
                        (proj_root / p).resolve() if not p.is_absolute() else p.resolve()
                    )
                    if lib_resolved == proj_resolved:
                        return ComponentLayer.PROJECT
                except (OSError, ValueError, TypeError):
                    pass
    return None


def _try_get_built_in_layer(resolved: Path) -> ComponentLayer:
    """Fallback probe for built-in or package-shipped component layer."""
    del resolved
    return ComponentLayer.BUILT_IN


def classify_component_layer(
    path: Path | None,
    project_dir: Path,
    library_paths: list[Path | tuple],
    project_config_paths: list[str] | None = None,
    global_roots: Sequence[Path] | None = None,
) -> ComponentLayer:
    """Classify a component directory path into a ComponentLayer enum (HATS-525 / HATS-1506)."""
    if path is None:
        return ComponentLayer.BUILT_IN

    raw_path = Path(path) if isinstance(path, (str, Path)) else None
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError, TypeError):
        return ComponentLayer.BUILT_IN

    global_layer = _try_get_global_layer(raw_path, resolved, global_roots=global_roots)
    if global_layer is not None:
        return global_layer

    project_layer = _try_get_project_layer(
        resolved,
        project_dir=project_dir,
        library_paths=library_paths,
        project_config_paths=project_config_paths or [],
    )
    if project_layer is not None:
        return project_layer

    return _try_get_built_in_layer(resolved)
