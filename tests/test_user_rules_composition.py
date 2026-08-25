"""Unit tests for user-rules discovery and rendering (HATS-1203).

Two contracts: ``discover_user_rules`` finds the right files, and
``_compose_sections`` renders them into every surface's prompt. The e2e
counterpart (``tests/e2e/test_user_rules_delivery.py``) proves they reach a
real session; these pin the edges cheaply.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.materialize import compose_for_role, discover_user_rules
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG, user_rules_dir
from ai_hats.surfaces.claude.provider import ClaudeProvider
from ai_hats.surfaces.agy.provider import AgyProvider
from ai_hats.surfaces.cline.provider import ClineProvider
from ai_hats_core import CompositionResult


REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_DIR = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"

SECTION_USER_RULES = "## USER RULES"
SECTION_RULES = "## RULES"


def _write_rule(project: Path, stem: str, body: str = "body\n") -> Path:
    d = user_rules_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{stem}.md"
    target.write_text(body)
    return target


# --------------------------------------------------------------------- #
# discover_user_rules
# --------------------------------------------------------------------- #


def test_absent_directory_yields_empty(tmp_path: Path):
    """A project that never created ``user-rules/`` must not raise."""
    ProjectConfig(provider="claude", library_paths=[]).save(tmp_path / PROJECT_CONFIG)
    assert discover_user_rules(tmp_path) == ()


def test_empty_directory_yields_empty(tmp_path: Path):
    ProjectConfig(provider="claude", library_paths=[]).save(tmp_path / PROJECT_CONFIG)
    user_rules_dir(tmp_path).mkdir(parents=True)
    assert discover_user_rules(tmp_path) == ()


def test_only_markdown_is_picked_up(tmp_path: Path):
    ProjectConfig(provider="claude", library_paths=[]).save(tmp_path / PROJECT_CONFIG)
    _write_rule(tmp_path, "keep")
    (user_rules_dir(tmp_path) / "notes.txt").write_text("ignored")
    (user_rules_dir(tmp_path) / "README.rst").write_text("ignored")
    assert [p.name for p in discover_user_rules(tmp_path)] == ["keep.md"]


def test_results_are_name_sorted(tmp_path: Path):
    """Sorted so the composed prompt is byte-stable across runs — the
    show-prompt/session equality contract depends on it."""
    ProjectConfig(provider="claude", library_paths=[]).save(tmp_path / PROJECT_CONFIG)
    for stem in ("zulu", "alpha", "mike"):
        _write_rule(tmp_path, stem)
    assert [p.stem for p in discover_user_rules(tmp_path)] == ["alpha", "mike", "zulu"]


# --------------------------------------------------------------------- #
# CompositionResult.with_user_rules — frozen contract
# --------------------------------------------------------------------- #


def _bare_result() -> CompositionResult:
    return CompositionResult(name="r", priorities=[], rules=[], skills=[], injections=[])


def test_with_user_rules_defaults_to_empty():
    assert _bare_result().user_rules == ()


def test_with_user_rules_returns_copy_leaving_original_intact():
    """``rule_composition_value_contract §1``: derive, never mutate."""
    original = _bare_result()
    derived = original.with_user_rules([Path("a.md")])
    assert original.user_rules == ()
    assert derived.user_rules == (Path("a.md"),)
    assert derived is not original


def test_with_user_rules_normalizes_to_tuple():
    derived = _bare_result().with_user_rules(iter([Path("a.md")]))
    assert isinstance(derived.user_rules, tuple)


# --------------------------------------------------------------------- #
# Rendering — provider parity (V2)
# --------------------------------------------------------------------- #


@pytest.fixture
def maintainer_project(tmp_path: Path) -> Assembler:
    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[LIBRARY_DIR])
    asm.init()
    asm.set_role("maintainer", provider_name="claude")
    return asm


ALL_PROVIDERS = [ClaudeProvider, AgyProvider, ClineProvider]


@pytest.mark.parametrize("provider_cls", ALL_PROVIDERS, ids=lambda c: c.__name__)
def test_every_surface_renders_user_rules(maintainer_project, provider_cls):
    """HATS-1203 R2: the section lives in the shared ``_compose_sections``, so
    agy and cline carry it too — neither ever had a user-rules channel."""
    _write_rule(maintainer_project.project_dir, "team", "Ledger id required.\n")
    result = compose_for_role(maintainer_project, "maintainer")

    prompt = provider_cls().build_system_prompt(result)

    assert SECTION_USER_RULES in prompt
    assert "### team" in prompt
    assert "Ledger id required." in prompt


def test_user_rules_render_after_framework_rules(maintainer_project):
    """R6 layout: project rules read as the more specific layer."""
    _write_rule(maintainer_project.project_dir, "team")
    result = compose_for_role(maintainer_project, "maintainer")

    prompt = ClaudeProvider().build_system_prompt(result)

    assert 0 <= prompt.find(SECTION_RULES) < prompt.find(SECTION_USER_RULES)


def test_multiple_rules_each_get_their_own_heading(maintainer_project):
    _write_rule(maintainer_project.project_dir, "alpha", "A body\n")
    _write_rule(maintainer_project.project_dir, "beta", "B body\n")
    result = compose_for_role(maintainer_project, "maintainer")

    prompt = ClaudeProvider().build_system_prompt(result)

    assert prompt.count(SECTION_USER_RULES) == 1
    assert prompt.find("### alpha") < prompt.find("### beta")


def test_no_section_without_rules(maintainer_project):
    """The overwhelmingly common case — no bare header, no stray separator."""
    result = compose_for_role(maintainer_project, "maintainer")
    assert SECTION_USER_RULES not in ClaudeProvider().build_system_prompt(result)


def test_blank_rule_file_does_not_emit_a_section(maintainer_project):
    """A whitespace-only file is indistinguishable from no rule at all."""
    _write_rule(maintainer_project.project_dir, "empty", "   \n\n")
    result = compose_for_role(maintainer_project, "maintainer")
    assert SECTION_USER_RULES not in ClaudeProvider().build_system_prompt(result)


def test_unreadable_rule_is_skipped_not_fatal(maintainer_project):
    """A rule the process cannot read must not take the whole session down —
    the prompt is built on every launch, so raising here bricks the CLI."""
    good = _write_rule(maintainer_project.project_dir, "good", "Good body\n")
    bad = _write_rule(maintainer_project.project_dir, "bad", "Bad body\n")
    bad.chmod(0o000)
    try:
        result = compose_for_role(maintainer_project, "maintainer")
        prompt = ClaudeProvider().build_system_prompt(result)
    finally:
        bad.chmod(0o644)

    assert "Good body" in prompt
    assert "Bad body" not in prompt
    assert good.exists()
