"""Component provenance classification and layer enum (HATS-525)."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from .paths import user_home


class ComponentLayer(str, Enum):
    """Source layer of a component in the dependency tree."""

    BUILT_IN = "built-in"
    GLOBAL = "global"
    PROJECT = "project"


def _try_get_global_layer(resolved: Path) -> ComponentLayer | None:
    """Probe if path belongs to user-global library (~/.ai-hats)."""
    global_lib = (user_home() / ".ai-hats").resolve()
    if resolved.is_relative_to(global_lib):
        return ComponentLayer.GLOBAL
    return None


def _try_get_project_layer(
    resolved: Path,
    project_dir: Path,
    library_paths: list[Path | tuple],
    project_config_paths: list[str],
) -> ComponentLayer | None:
    """Probe if path belongs to project-local or project-configured library."""
    global_lib = (user_home() / ".ai-hats").resolve()
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

        if lib_resolved != global_lib and resolved.is_relative_to(lib_resolved):
            if lib_resolved.is_relative_to(proj_root):
                return ComponentLayer.PROJECT
            for proj_p in project_config_paths:
                try:
                    if lib_resolved == Path(proj_p).expanduser().resolve():
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
) -> ComponentLayer:
    """Classify a resolved component directory path into a ComponentLayer enum (HATS-525)."""
    if path is None:
        return ComponentLayer.BUILT_IN

    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError, TypeError):
        return ComponentLayer.BUILT_IN

    global_layer = _try_get_global_layer(resolved)
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
