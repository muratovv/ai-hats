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


def build_library_paths(
    project_dir: Path,
    *,
    config_paths: Sequence[str | Path] = (),
    local_libraries: Path | None = None,
    extra: Sequence[Path] = (),
) -> list[Path]:
    """Ordered library roots, earlier = lower priority (``LibraryResolver`` last-wins).

    Built-in shipping (``core`` then ``usage``) first; override points —
    entry-point packages, user-global, config-specified, project-local, explicit
    ``extra`` — layer on top. ``local_libraries`` overrides the project-local
    layer (the worktree re-point, HATS-831); ``None`` means
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

    for configured in config_paths:
        expanded = Path(configured).expanduser()
        if expanded.is_dir():
            paths.append(expanded)

    local_lib = local_libraries or project_dir / LIBRARIES_DIRNAME
    if local_lib.is_dir():
        paths.append(local_lib)

    paths.extend(extra)
    return paths
