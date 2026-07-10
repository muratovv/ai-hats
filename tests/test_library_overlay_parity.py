"""Override-layer parity after the library package move (HATS-876 / T18, Q2/Q3).

The move repointed ONLY the built-in source (packages/ai-hats-library). The
last-wins overlay stack above it — built-in < ~/.ai-hats < library_paths <
project-local — must be unchanged: a custom/project overlay still shadows a
built-in component, and a built-in-only component still resolves through the
moved package. Guards against the seam collapsing the stack to the package alone.
"""

from __future__ import annotations

from ai_hats.models import ComponentType
from ai_hats.paths import builtin_library_layers
from ai_hats.resolver import LibraryResolver


def _a_builtin_skill(layer) -> str:
    return next(d.name for d in (layer / "skills").iterdir() if d.is_dir())


def test_overlay_shadows_builtin_and_builtin_still_resolves(tmp_path):
    layers = builtin_library_layers()
    assert layers, "built-in layers must resolve from the moved package"
    core = layers[0]

    skills = sorted(d.name for d in (core / "skills").iterdir() if d.is_dir())
    shadowed, builtin_only = skills[0], skills[1]

    # A project overlay that redefines `shadowed`.
    overlay = tmp_path / "libraries"
    (overlay / "skills" / shadowed).mkdir(parents=True)
    (overlay / "skills" / shadowed / "SKILL.md").write_text("OVERLAY", encoding="utf-8")

    resolver = LibraryResolver([*layers, overlay])

    # last-wins: the overlay redefinition beats the built-in.
    assert resolver.resolve(shadowed, ComponentType.SKILL) == overlay / "skills" / shadowed
    # a built-in-only name still resolves through the moved package (not dropped).
    resolved = resolver.resolve(builtin_only, ComponentType.SKILL)
    assert resolved is not None
    assert str(resolved).startswith(str(core))
