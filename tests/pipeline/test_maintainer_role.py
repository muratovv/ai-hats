"""HATS-381 — assert maintainer role + ai-hats-framework + personal-workflow
traits ship in the library and carry the promoted MEMORY content.

These are static-shape assertions on the YAML configs and bundled SKILL.md /
rule.md bodies. No subprocess, no real bump. The E2E gate does not apply
(no CLI/shell/pip surface changes).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.models import ComponentConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY = REPO_ROOT / "library"


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def _load(rel: str) -> ComponentConfig:
    return ComponentConfig.from_yaml(REPO_ROOT / rel)


# --- New components exist ---------------------------------------------------


def test_maintainer_role_exists() -> None:
    role = _load("library/usage/roles/maintainer/config.yaml")
    assert role.name == "maintainer"


def test_ai_hats_framework_trait_exists() -> None:
    trait = _load("library/core/traits/ai-hats-framework/config.yaml")
    assert trait.name == "ai-hats-framework"


def test_personal_workflow_trait_exists() -> None:
    trait = _load("library/usage/traits/personal-workflow/config.yaml")
    assert trait.name == "personal-workflow"


def test_rule_core_vs_usage_split_exists() -> None:
    rule_md = (LIBRARY / "core/rules/rule_core_vs_usage_split/rule.md").read_text()
    assert "Core vs Usage" in rule_md
    assert "universal" in rule_md.lower()
    assert "project-specific" in rule_md.lower()


@pytest.mark.parametrize(
    "skill_rel",
    [
        "library/core/skills/design-minimalism/SKILL.md",
        "library/core/skills/predictive-accounting/SKILL.md",
        "library/usage/skills/doc-protocol/SKILL.md",
    ],
)
def test_new_skill_exists_with_frontmatter(skill_rel: str) -> None:
    body = _read(skill_rel)
    assert body.startswith("---\n"), "skill must start with YAML frontmatter"
    assert "name:" in body
    assert "description:" in body


# --- Maintainer composition (10 traits, expected list) ---------------------


def test_maintainer_composition_has_expected_traits() -> None:
    role = _load("library/usage/roles/maintainer/config.yaml")
    expected = {
        "trait-base",
        "trait-agent",
        "trait-se-mindset",
        "trait-researcher-mindset",
        "skill-engineer",
        "ai-hats-maintainer",
        "ai-hats-framework",
        "personal-workflow",
        "dev::python",
        "dev::shell",
    }
    assert set(role.composition.traits) == expected


def test_maintainer_injection_has_role_header() -> None:
    role = _load("library/usage/roles/maintainer/config.yaml")
    assert "AI-HATS MAINTAINER" in role.injection


# --- ai-hats-maintainer trait — promoted content present --------------------


@pytest.mark.parametrize(
    "needle",
    [
        # CONTRIBUTING-derived policies
        "Conventional Commits",
        "What NOT to commit",
        "Branches and commits",
        # Canonical CLI
        "ai-hats update",
        # Glossary-first + numbered-refs
        "Glossary-first",
        "Numbered-refs",
        # D2 diagrams pointer
        "Diagrams (d2)",
        # Architectural defaults (design preferences)
        "Strict typed contracts",
        "Immutable state",
        "Open registries",
        "Bash-composable",
        # Anti-patterns (hyp_vs_harness_risks, testability, memory dedup)
        "Memory pattern duplication",
        "HYP-as-technical-risk",
    ],
)
def test_ai_hats_maintainer_injection_contains(needle: str) -> None:
    trait = _load("library/usage/traits/ai-hats-maintainer/config.yaml")
    assert needle in trait.injection, f"missing in ai-hats-maintainer injection: {needle!r}"


def test_ai_hats_maintainer_attaches_doc_protocol() -> None:
    trait = _load("library/usage/traits/ai-hats-maintainer/config.yaml")
    assert "doc-protocol" in trait.composition.skills


def test_ai_hats_maintainer_keeps_e2e_gate_rule() -> None:
    trait = _load("library/usage/traits/ai-hats-maintainer/config.yaml")
    assert "dev_rule_e2e_gate" in trait.composition.rules


# --- ai-hats-framework trait — wraps the new rule --------------------------


def test_ai_hats_framework_attaches_core_vs_usage_rule() -> None:
    trait = _load("library/core/traits/ai-hats-framework/config.yaml")
    assert "rule_core_vs_usage_split" in trait.composition.rules


def test_ai_hats_framework_injection_mentions_layered_library() -> None:
    trait = _load("library/core/traits/ai-hats-framework/config.yaml")
    inj = trait.injection
    assert "library/core/" in inj
    assert "library/usage/" in inj
    assert "universal" in inj.lower()


# --- personal-workflow trait — plan-iteration content present --------------


def test_personal_workflow_has_plan_iteration_content() -> None:
    trait = _load("library/usage/traits/personal-workflow/config.yaml")
    inj = trait.injection
    assert "Plan-mode iteration hygiene" in inj
    assert "review-comments" in inj or "review-marker" in inj.lower()


def test_personal_workflow_marked_temporary() -> None:
    # The YAML carries an inline NOTE-comment flagging the trait as temporary;
    # this preserves the "remove when user-skill-install lands" exit criterion.
    raw = (LIBRARY / "usage/traits/personal-workflow/config.yaml").read_text()
    assert "TEMPORARY" in raw.upper()


# --- Existing-trait wiring (skill attachments) -----------------------------


def test_trait_agent_attaches_design_minimalism() -> None:
    trait = _load("library/core/traits/trait-agent/config.yaml")
    assert "design-minimalism" in trait.composition.skills


def test_trait_se_mindset_attaches_predictive_accounting() -> None:
    trait = _load("library/usage/traits/trait-se-mindset/config.yaml")
    assert "predictive-accounting" in trait.composition.skills


# --- Initial-wizard wiring -------------------------------------------------


def test_initial_wizard_includes_ai_hats_framework_trait() -> None:
    role = _load("library/core/roles/initial-wizard/config.yaml")
    assert "ai-hats-framework" in role.composition.traits


# --- Rule extensions (existing rules, new content) -------------------------


def test_rule_backlog_discipline_has_hyp_vs_technical_risk() -> None:
    body = _read("library/core/rules/rule_backlog_discipline/rule.md")
    assert "HYP vs technical risk" in body
    assert "agent-behaviour" in body or "agent-behavior" in body


def test_scope_guard_has_skeleton_signal_bullet() -> None:
    body = _read("library/core/skills/scope-guard/SKILL.md")
    assert "skeleton" in body.lower()
    assert "infrastructure" in body.lower() or "minimum viable" in body.lower()


# --- Docs ------------------------------------------------------------------


def test_glossary_has_reflect_role_mapping_table() -> None:
    body = _read("docs/glossary.md")
    # Compact table introduced by HATS-381 promoting reference_ai_hats_reflect_roles.
    assert "ai-hats reflect roles" in body
    assert "auditor-for-role" in body
    assert "judge-for-role" in body


def test_contributing_has_maintainer_pointer() -> None:
    body = _read("CONTRIBUTING.md")
    # Pointer must be near the top so agents see it without scrolling.
    head = "\n".join(body.splitlines()[:20])
    assert "maintainer" in head.lower()
    assert "ai-hats config set -r maintainer" in head or "ai-hats config" in head
