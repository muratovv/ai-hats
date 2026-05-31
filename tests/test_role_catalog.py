"""Tests for the live role catalog injected into the wizard prompt (HATS-625).

Two layers of guarantee:
- **Hermetic exact-set** (``fixture_resolver``): a tmp library with a known
  role set across core/usage/user layers — asserts ``render_role_catalog``
  emits EXACTLY the intended user-facing roles, decoupled from the real
  catalog's growth.
- **Real-library property** (``test_wizard_session_prompt_*``): the actual
  ``initial-wizard`` composed + run through ``build_session_prompt`` lists
  real user-facing roles (``dev-web``) and excludes engine-internal ones
  (``judge``) — catches real-catalog regressions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_hats.models import ProjectConfig
from ai_hats.resolver import LibraryResolver
from ai_hats.role_catalog import (
    ROLE_CATALOG_PLACEHOLDER,
    _layer_of,
    _summary_from_injection,
    expand_role_catalog,
    render_role_catalog,
)


# --------------------------------------------------------------------- #
# fixture library
# --------------------------------------------------------------------- #
def _write_role(libroot: Path, name: str, injection: str, priorities: list[str]) -> None:
    role_dir = libroot / "roles" / name
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "priorities": priorities,
                "composition": {"traits": [], "rules": [], "skills": []},
                "injection": injection,
            },
            sort_keys=False,
        )
    )


@pytest.fixture
def fixture_resolver(tmp_path):
    """Known role set across the three layers (libroot name = layer)."""
    core, usage, userlib = tmp_path / "core", tmp_path / "usage", tmp_path / "userlib"
    _write_role(core, "wizard", "# ROLE: WIZARD\n\nbootstrap mentor.", ["User-clarity"])
    _write_role(core, "judge", "# ROLE: JUDGE\n\njudge verdicts.", ["Decisiveness"])
    _write_role(usage, "foo", "# ROLE: FOO DEVELOPER\n\nfoo prose.", ["Correctness", "Speed"])
    _write_role(usage, "bar", "# ROLE: BAR\n\nbar prose.", ["Quality"])
    _write_role(userlib, "baz", "# ROLE: BAZ\n\nbaz prose.", ["Velocity"])
    return LibraryResolver([core, usage, userlib])


# --------------------------------------------------------------------- #
# _summary_from_injection
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "injection,expected",
    [
        ("# ROLE: WEB / FRONTEND DEVELOPER\n\nYou build UIs.", "WEB / FRONTEND DEVELOPER"),
        ("# Some Heading\n\nbody", "Some Heading"),
        ("no heading here\nsecond line", "no heading here"),  # fallback: first non-empty
        ("", ""),
        ("\n\n   \n", ""),  # only blanks
    ],
)
def test_summary_from_injection(injection, expected):
    assert _summary_from_injection(injection) == expected


# --------------------------------------------------------------------- #
# _layer_of
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path,expected",
    [
        (Path("/x/ai_hats/library/core/roles/judge"), "core"),
        (Path("/x/ai_hats/library/usage/roles/dev-web"), "usage"),
        (Path("/home/u/.ai-hats/roles/myrole"), "user"),
        (Path("/proj/libraries/roles/baz"), "user"),
    ],
)
def test_layer_of(path, expected):
    assert _layer_of(path) == expected


# --------------------------------------------------------------------- #
# render_role_catalog — hermetic exact set
# --------------------------------------------------------------------- #
def _names(catalog: str) -> list[str]:
    import re

    return [m.group(1) for m in re.finditer(r"- \*\*(.+?)\*\*", catalog)]


def test_render_user_facing_exact_set(fixture_resolver):
    catalog = render_role_catalog(fixture_resolver, user_facing=True)
    # EXACTLY the non-core roles, sorted; judge + wizard (core) excluded.
    assert _names(catalog) == ["bar", "baz", "foo"]


def test_render_carries_summary_and_priorities(fixture_resolver):
    catalog = render_role_catalog(fixture_resolver, user_facing=True)
    assert "- **foo** — FOO DEVELOPER · _Correctness, Speed_" in catalog


def test_render_all_includes_core(fixture_resolver):
    catalog = render_role_catalog(fixture_resolver, user_facing=False)
    assert _names(catalog) == ["bar", "baz", "foo", "judge", "wizard"]


# --------------------------------------------------------------------- #
# expand_role_catalog — no-op fast path
# --------------------------------------------------------------------- #
def test_expand_noop_without_placeholder(tmp_path):
    # No placeholder → returned unchanged WITHOUT building an Assembler
    # (tmp_path has no ai-hats.yaml; building one would raise).
    text = "## Some prompt\nno placeholder here.\n"
    assert expand_role_catalog(text, tmp_path) == text


# --------------------------------------------------------------------- #
# real-library property test (through build_session_prompt)
# --------------------------------------------------------------------- #
# Editable installs map `ai_hats.library` to the INSTALL location, not this
# worktree. Point the project's library_paths at the worktree's library so
# composition AND expand_role_catalog see the in-progress wizard + roles
# (library-curator recipe). builtin layers stay underneath; worktree wins
# last (override).
_WT_LIBRARY = Path(__file__).resolve().parents[1] / "library"
_WT_LIBRARY_PATHS = [str(_WT_LIBRARY / "core"), str(_WT_LIBRARY / "usage")]


def test_wizard_session_prompt_lists_live_roles(tmp_path):
    """The composed initial-wizard prompt carries the live catalog."""
    from ai_hats.assembler import Assembler
    from ai_hats.providers import ClaudeProvider

    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(provider="claude", library_paths=_WT_LIBRARY_PATHS).save(
        project / "ai-hats.yaml"
    )
    asm = Assembler(project)
    asm.init()

    result = asm.composer.compose("initial-wizard")
    _, _, content = ClaudeProvider().build_session_prompt(project, result, "sid-xyz")

    # placeholder fully expanded
    assert ROLE_CATALOG_PLACEHOLDER not in content
    # user-facing roles present (catalog line format)
    assert "- **dev-web**" in content
    assert "- **assistant**" in content
    assert "- **dev-python**" in content  # guarantee moved from test_role_split
    # engine-internal (core) roles excluded
    assert "- **judge**" not in content
    assert "- **auditor-for-role**" not in content
    assert "- **initial-wizard**" not in content


def test_non_wizard_prompt_has_no_catalog(tmp_path):
    """A role without the placeholder is unaffected (no catalog injected)."""
    from ai_hats.assembler import Assembler
    from ai_hats.providers import ClaudeProvider

    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(provider="claude", library_paths=_WT_LIBRARY_PATHS).save(
        project / "ai-hats.yaml"
    )
    asm = Assembler(project)
    asm.init()

    result = asm.composer.compose("assistant")
    _, _, content = ClaudeProvider().build_session_prompt(project, result, "sid-2")

    assert ROLE_CATALOG_PLACEHOLDER not in content
    assert "- **dev-web**" not in content  # no catalog block in a normal role
