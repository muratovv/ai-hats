"""The one trust procedure, exercised through both keys that travel with the pin.

ADR-0025 D3. The point is the *shared* function: before HATS-1613 `AI_HATS_DIR`
was pair-scoped while `AI_HATS_VENV` was taken raw — the Python mirror of the
`dispatcher.sh:28-30` bug behind HATS-1525. Parametrising one table over both
keys is what makes a future third key inherit the answer instead of inventing one.
"""  # comment-length: allow — states why the table is shared

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

from ai_hats.cli._entry import project_at
from ai_hats.env import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR, ENV_AI_HATS_VENV
from ai_hats_core.layout import ProjectLayout

# (env var, callable(project_dir) -> resolved path, override value builder)
KEYS = [
    pytest.param(
        ENV_AI_HATS_DIR,
        lambda project: ProjectLayout.compute(project, os.environ).base,
        id="AI_HATS_DIR",
    ),
    pytest.param(
        ENV_AI_HATS_VENV,
        lambda project: project_at(project, os.environ).venv,
        id="AI_HATS_VENV",
    ),
]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".agent" / "ai-hats").mkdir(parents=True)
    return root


def _resolve(resolver, project: Path) -> tuple[Path, list[warnings.WarningMessage]]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = resolver(project)
    return value, [w for w in caught if "foreign" in str(w.message)]


@pytest.mark.parametrize(("var", "resolver"), KEYS)
def test_bare_override_wins(var, resolver, project, tmp_path, monkeypatch):
    """No pin at all → env-wins. An unpaired key is a human's explicit override."""
    override = tmp_path / "elsewhere"
    monkeypatch.delenv(AI_HATS_PROJECT_DIR_ENV, raising=False)
    monkeypatch.setenv(var, str(override))

    value, foreign = _resolve(resolver, project)

    assert value == override
    assert not foreign, "a bare override must not be treated as a leaked pin"


@pytest.mark.parametrize(("var", "resolver"), KEYS)
def test_matching_pin_is_honoured_silently(var, resolver, project, tmp_path, monkeypatch):
    """Pin agrees → honoured, and NO warning: noise on the happy path stops being read."""
    override = tmp_path / "elsewhere"
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(project))
    monkeypatch.setenv(var, str(override))

    value, foreign = _resolve(resolver, project)

    assert value == override
    assert not foreign, f"guard fired on a same-project pin: {[str(w.message) for w in foreign]}"


@pytest.mark.parametrize(("var", "resolver"), KEYS)
def test_foreign_pin_drops_the_override_and_warns(var, resolver, project, tmp_path, monkeypatch):
    """Pin names another project → the override is dropped, loudly."""
    override = tmp_path / "elsewhere"
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(tmp_path / "other-project"))
    monkeypatch.setenv(var, str(override))

    value, foreign = _resolve(resolver, project)

    assert value != override, f"{var} honoured a pin belonging to another project"
    assert str(project) in str(value), "resolution must fall back inside this project"
    assert foreign, "dropping an override silently is the defect, not the fix"
    assert var in str(foreign[0].message), "the warning must name the key it dropped"


@pytest.mark.parametrize(("var", "resolver"), KEYS)
def test_pin_comparison_survives_a_non_normalised_path(var, resolver, project, monkeypatch):
    """`P == R` is decided after normalisation, not by string equality."""
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(project / "sub" / ".."))
    monkeypatch.setenv(var, str(project / "chosen"))

    _, foreign = _resolve(resolver, project)

    assert not foreign, "an unnormalised spelling of the same root must not read as foreign"
