"""HATS-700 — rule-delivery contract.

The library under test is ALWAYS the source tree this test file lives in
(worktree-safe). We resolve it via ``Path(__file__).parent.parent / "library"``
and an explicit ``LibraryResolver`` — never the editable-install builtin library
(``importlib.resources.files("ai_hats.library")``), which is baked to whichever
checkout ran ``pip install -e`` and would mask worktree edits (the standard
worktree-safe library-resolution idiom used across the real-library tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.composer import Composer
from ai_hats.surfaces.claude.provider import ClaudeProvider
from ai_hats_agy.provider import AgyProvider
from ai_hats.resolver import LibraryResolver
from ai_hats.rule_delivery import find_dangling_rule_pointers

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_LAYERS = [
    REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library" / "core",
    REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library" / "usage",
]
PROVIDERS = [ClaudeProvider, AgyProvider]


def _resolver() -> LibraryResolver:
    return LibraryResolver(LIB_LAYERS)


def _lib_text(*parts: str) -> str:
    return (
        REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library" / Path(*parts)
    ).read_text()


# --------------------------------------------------------------------------- #
# Re-homing regression (step 9): the two non-always-on gaps closed by HATS-700.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("trait", ["trait-base", "trait-analyst-base"])
def test_harness_reminder_essence_delivered_in_both_base_traits(trait):
    cfg = _lib_text("core", "traits", trait, "config.yaml")
    assert "Harness Reminders" in cfg, f"{trait} missing the harness-reminder bullet"
    assert "rule_harness_reminder_hygiene" in cfg


def test_edit_efficiency_folded_into_skill_and_rule_removed():
    assert not (
        REPO_ROOT
        / "packages"
        / "ai-hats-library"
        / "src"
        / "ai_hats_library"
        / "core"
        / "rules"
        / "dev_rule_edit_efficiency"
    ).exists(), "dev_rule_edit_efficiency rule dir should be deleted"
    assert "dev_rule_edit_efficiency" not in _lib_text(
        "core", "traits", "trait-agent", "config.yaml"
    )
    skill = _lib_text("core", "skills", "tool-call-hygiene", "SKILL.md")
    assert "Edit efficiency" in skill
    assert "3+ Edits" in skill


def test_maintainer_prompt_delivers_harness_bullet_not_edit_efficiency():
    result = Composer(_resolver()).compose("maintainer")
    assert result.errors == []
    prompt = ClaudeProvider().build_system_prompt(result)
    assert "Harness Reminders" in prompt
    assert "rule_harness_reminder_hygiene" in prompt
    assert "dev_rule_edit_efficiency" not in {r.name for r in result.rules}


# --------------------------------------------------------------------------- #
# G1 — every composed rule's body reaches the prompt for BOTH providers.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("provider_cls", PROVIDERS)
@pytest.mark.parametrize(
    "rule_name",
    ["global_rule_destructive_actions", "dev_rule_secure_coding", "rule_backlog_discipline"],
)
def test_composed_rule_body_reaches_prompt(rule_name, provider_cls):
    resolver = _resolver()
    rule_dir = resolver.resolve_rule_dir(rule_name)
    assert rule_dir is not None, f"rule {rule_name} absent from library"

    rule = ResolvedComponent(
        name=rule_name,
        component_type=ComponentKind.RULE,
        source_path=rule_dir,
    )
    result = CompositionResult(
        name="g1",
        priorities=[],
        rules=[rule],
        skills=[],
        injections=[],
    )
    prompt = provider_cls().build_system_prompt(result)

    assert "## RULES" in prompt
    assert f"### {rule_name}" in prompt
    body = prompt.split(f"### {rule_name}", 1)[1]
    assert body.strip(), f"{rule_name} heading present but body empty (lazy load broke)"


# --------------------------------------------------------------------------- #
# G2 — no `see rule X` pointer reaches the agent for a rule that does not exist.
# --------------------------------------------------------------------------- #


def test_no_dangling_rule_pointers_in_shipped_library():
    violations = find_dangling_rule_pointers(
        REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
    )
    assert violations == [], (
        "Dangling `see rule X` pointers — each rule must exist in library:\n"
        + "\n".join(f"  {v.source}: see rule `{v.rule}`" for v in violations)
    )


def test_g2_catches_an_undelivered_pointer(tmp_path):
    # Sanity: the gate is not vacuous. A trait that points at a non-existent rule must be flagged.
    trait = tmp_path / "core" / "traits" / "trait-bad"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: trait-bad\ninjection: |\n  Do the thing — see rule `rule_totally_undelivered`.\n"
    )
    violations = find_dangling_rule_pointers(tmp_path)
    assert [v.rule for v in violations] == ["rule_totally_undelivered"]


# --------------------------------------------------------------------------- #
# HATS-1836: a leftover rule metadata.yaml is inert
# --------------------------------------------------------------------------- #


def test_legacy_delivery_key_in_sidecar_is_inert(tmp_path):
    """An external library's leftover `delivery:` key changes nothing about delivery."""
    rule_dir = tmp_path / "custom_rule"
    rule_dir.mkdir()
    (rule_dir / "metadata.yaml").write_text("name: custom_rule\ndelivery: always_on\n")
    (rule_dir / "rule.md").write_text("### custom_rule\nBody of custom rule.\n")

    rule = ResolvedComponent(
        name="custom_rule",
        component_type=ComponentKind.RULE,
        source_path=rule_dir,
    )
    result = CompositionResult(
        name="opt_in_test",
        priorities=[],
        rules=[rule],
        skills=[],
        injections=[],
    )
    prompt = ClaudeProvider().build_system_prompt(result)

    assert "## RULES" in prompt
    assert "### custom_rule" in prompt
    assert "Body of custom rule." in prompt


def test_rule_with_empty_body_warns(tmp_path, caplog):
    """HATS-1511: Rule with empty/missing body issues warning."""
    import logging

    rule_dir = tmp_path / "empty_rule"
    rule_dir.mkdir()
    (rule_dir / "rule.md").write_text("")

    rule = ResolvedComponent(
        name="empty_rule",
        component_type=ComponentKind.RULE,
        source_path=rule_dir,
    )
    result = CompositionResult(
        name="empty_body_test",
        priorities=[],
        rules=[rule],
        skills=[],
        injections=[],
    )

    with caplog.at_level(logging.WARNING):
        prompt = ClaudeProvider().build_system_prompt(result)

    assert "### empty_rule" not in prompt
    assert "rule 'empty_rule': body is empty or unreadable" in caplog.text


def test_malformed_sidecar_cannot_affect_prompt_build(tmp_path):
    """HATS-1836: a rule's metadata.yaml is never parsed, so even invalid YAML is harmless."""
    rule_dir = tmp_path / "bad_meta_rule"
    rule_dir.mkdir()
    (rule_dir / "metadata.yaml").write_text("name: : : invalid yaml syntax [[[\n")
    (rule_dir / "rule.md").write_text("Body text.\n")

    rule = ResolvedComponent(
        name="bad_meta_rule",
        component_type=ComponentKind.RULE,
        source_path=rule_dir,
    )
    result = CompositionResult(
        name="bad_meta_test",
        priorities=[],
        rules=[rule],
        skills=[],
        injections=[],
    )

    prompt = ClaudeProvider().build_system_prompt(result)

    assert "### bad_meta_rule" in prompt
    assert "Body text." in prompt


# --------------------------------------------------------------------------- #
# HATS-1514: Dangling pointer checker fixes (hyphens, composition.rules, multi-root, HATS-1511 opt-in)
# --------------------------------------------------------------------------- #


def test_dangling_pointer_with_hyphens_detected(tmp_path):
    """HATS-1514 Gap 1: pointers with hyphens in rule names are detected."""
    trait = tmp_path / "core" / "traits" / "trait-hyphen"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: trait-hyphen\ninjection: |\n  Follow policy — see rule `no-leverage`.\n"
    )
    violations = find_dangling_rule_pointers(tmp_path)
    assert [v.rule for v in violations] == ["no-leverage"]


def test_undelivered_composition_rule_detected(tmp_path):
    """HATS-1514 Gap 3: undelivered rule declared under composition.rules: is detected."""
    trait = tmp_path / "core" / "traits" / "trait-comp"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: trait-comp\ncomposition:\n  rules:\n    - undelivered-rule\n"
    )
    violations = find_dangling_rule_pointers(tmp_path)
    assert [v.rule for v in violations] == ["undelivered-rule"]


def test_find_dangling_pointers_scans_multiple_library_roots(tmp_path):
    """HATS-1514 Gap 2: find_dangling_rule_pointers accepts a list of library roots."""
    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"
    trait1 = root1 / "traits" / "t1"
    trait2 = root2 / "traits" / "t2"
    trait1.mkdir(parents=True)
    trait2.mkdir(parents=True)

    (trait1 / "config.yaml").write_text("name: t1\ninjection: |\n  see rule `dangling-one`.\n")
    (trait2 / "config.yaml").write_text("name: t2\ninjection: |\n  see rule `dangling-two`.\n")

    violations = find_dangling_rule_pointers([root1, root2])
    rules = [v.rule for v in violations]
    assert "dangling-one" in rules
    assert "dangling-two" in rules


def test_sidecarless_rule_not_flagged_as_dangling(tmp_path):
    """HATS-1514 regression: a rule dir carrying only rule.md is deliverable and not flagged."""
    lib = tmp_path / "lib"
    trait_dir = lib / "traits" / "t_opt"
    rule_dir = lib / "rules" / "opt-in-rule"
    trait_dir.mkdir(parents=True)
    rule_dir.mkdir(parents=True)

    (rule_dir / "rule.md").write_text("Rule body.\n")

    (trait_dir / "config.yaml").write_text(
        "name: t_opt\ncomposition:\n  rules:\n    - opt-in-rule\ninjection: |\n  see rule `opt-in-rule`.\n"
    )

    violations = find_dangling_rule_pointers(lib)
    assert violations == []


def test_main_cli_entry_accepts_multiple_args(tmp_path):
    """HATS-1514 Gap 2: _main accepts multiple root path arguments."""
    from ai_hats.rule_delivery import _main

    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"
    trait1 = root1 / "traits" / "t1"
    trait2 = root2 / "traits" / "t2"
    trait1.mkdir(parents=True)
    trait2.mkdir(parents=True)

    (trait1 / "config.yaml").write_text("name: t1\ninjection: |\n  see rule `dangling-one`.\n")
    (trait2 / "config.yaml").write_text("name: t2\ninjection: |\n  see rule `dangling-two`.\n")

    rc = _main([str(root1), str(root2)])
    assert rc == 1


def test_find_dangling_pointers_default_scans_user_global_library_paths(tmp_path, monkeypatch):
    """HATS-1514 Leader Remark 1: default invocation scans user-global library_paths.yaml."""
    user_home_dir = tmp_path / "user_home"
    ai_hats_dir = user_home_dir / ".ai-hats"
    ai_hats_dir.mkdir(parents=True)

    custom_lib = tmp_path / "custom_lib"
    custom_trait = custom_lib / "traits" / "t_custom"
    custom_trait.mkdir(parents=True)
    (custom_trait / "config.yaml").write_text(
        "name: t_custom\ninjection: |\n  see rule `custom-dangling`.\n"
    )

    (ai_hats_dir / "library_paths.yaml").write_text(f"paths:\n  - {custom_lib}\n")
    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home_dir))

    violations = find_dangling_rule_pointers(None, project_dir=tmp_path)
    rules = [v.rule for v in violations]
    assert "custom-dangling" in rules
