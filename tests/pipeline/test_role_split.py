"""HATS-381 — assert maintainer role + ai-hats-framework trait ship in the
library and carry the promoted MEMORY content.

HATS-433 removed the `personal-workflow` trait from the library — it now
lives in user-scope (`~/.ai-hats/traits/personal-workflow/`) and is layered
in via `ai-hats config customize <role> --add-trait personal-workflow
--global`. Tests for that trait's content moved to the user's own concern;
this file only asserts that the trait is NO LONGER present in the library.

These are static-shape assertions on the YAML configs and bundled SKILL.md /
rule.md bodies. No subprocess, no real bump. The E2E gate does not apply
(no CLI/shell/pip surface changes).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.models import ComponentConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def _load(rel: str) -> ComponentConfig:
    return ComponentConfig.from_yaml(REPO_ROOT / rel)


# --- New components exist ---------------------------------------------------


def test_maintainer_role_exists() -> None:
    role = _load("packages/ai-hats-library/src/ai_hats_library/usage/roles/maintainer/config.yaml")
    assert role.name == "maintainer"


def test_ai_hats_framework_trait_exists() -> None:
    trait = _load("packages/ai-hats-library/src/ai_hats_library/core/traits/ai-hats-framework/config.yaml")
    assert trait.name == "ai-hats-framework"


def test_personal_workflow_trait_removed_from_library() -> None:
    """HATS-433: trait migrated to user-scope; library copy must be gone."""
    assert not (LIBRARY / "usage/traits/personal-workflow").exists()


def test_rule_core_vs_usage_split_exists() -> None:
    rule_md = (LIBRARY / "core/rules/rule_core_vs_usage_split/rule.md").read_text()
    assert "Core vs Usage" in rule_md
    assert "universal" in rule_md.lower()
    assert "project-specific" in rule_md.lower()


@pytest.mark.parametrize(
    "skill_rel",
    [
        "packages/ai-hats-library/src/ai_hats_library/core/skills/design-minimalism/SKILL.md",
        "packages/ai-hats-library/src/ai_hats_library/core/skills/predictive-accounting/SKILL.md",
        "packages/ai-hats-library/src/ai_hats_library/usage/skills/doc-protocol/SKILL.md",
    ],
)
def test_new_skill_exists_with_frontmatter(skill_rel: str) -> None:
    body = _read(skill_rel)
    assert body.startswith("---\n"), "skill must start with YAML frontmatter"
    assert "name:" in body
    assert "description:" in body


# --- Maintainer composition (10 traits, expected list) ---------------------


def test_maintainer_composition_has_expected_traits() -> None:
    role = _load("packages/ai-hats-library/src/ai_hats_library/usage/roles/maintainer/config.yaml")
    # HATS-433: personal-workflow dropped — now layered via user-scope `--global`.
    expected = {
        "trait-base",
        "trait-agent",
        "trait-se-mindset",
        "trait-researcher-mindset",
        "skill-engineer",
        "ai-hats-maintainer",
        "ai-hats-framework",
        "dev::python",
        "dev::shell",
    }
    assert set(role.composition.traits) == expected


def test_maintainer_injection_has_role_header() -> None:
    role = _load("packages/ai-hats-library/src/ai_hats_library/usage/roles/maintainer/config.yaml")
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
        "ai-hats self update",
        # Glossary-first + numbered-refs
        "Glossary-first",
        "Numbered-refs",
        # D2 diagrams — promoted reference_d2_label_syntax content
        "Diagrams (d2)",
        "Multiline labels",
        "Theme overrides",
        "Palette slot map",
        "_palette.d2",
        "Source Code Pro",
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
    trait = _load("packages/ai-hats-library/src/ai_hats_library/usage/traits/ai-hats-maintainer/config.yaml")
    assert needle in trait.injection, f"missing in ai-hats-maintainer injection: {needle!r}"


def test_ai_hats_maintainer_attaches_doc_protocol() -> None:
    trait = _load("packages/ai-hats-library/src/ai_hats_library/usage/traits/ai-hats-maintainer/config.yaml")
    assert "doc-protocol" in trait.composition.skills


def test_ai_hats_maintainer_keeps_e2e_gate_rule() -> None:
    trait = _load("packages/ai-hats-library/src/ai_hats_library/usage/traits/ai-hats-maintainer/config.yaml")
    assert "dev_rule_e2e_gate" in trait.composition.rules


# --- ai-hats-framework trait — injection only, rule lives on library-curator --


def test_ai_hats_framework_does_not_carry_core_vs_usage_rule() -> None:
    """HATS-510 (B): ``rule_core_vs_usage_split`` ownership moved to the
    ``library-curator`` trait (role-curator domain). ``ai-hats-framework``
    keeps the framework-awareness *injection* but carries no formal
    constraint — non-trivial library work hands off to role-curator."""
    framework = _load("packages/ai-hats-library/src/ai_hats_library/core/traits/ai-hats-framework/config.yaml")
    assert "rule_core_vs_usage_split" not in framework.composition.rules

    curator = _load("packages/ai-hats-library/src/ai_hats_library/usage/traits/library-curator/config.yaml")
    assert "rule_core_vs_usage_split" in curator.composition.rules


def test_ai_hats_framework_injection_mentions_layered_library() -> None:
    trait = _load("packages/ai-hats-library/src/ai_hats_library/core/traits/ai-hats-framework/config.yaml")
    inj = trait.injection
    assert "library/core/" in inj
    assert "library/usage/" in inj
    assert "universal" in inj.lower()


# --- personal-workflow trait — content lives in user-scope (HATS-433) -----
# Removed: `test_personal_workflow_has_plan_iteration_content` and
# `test_personal_workflow_marked_temporary`. The trait is no longer in the
# library — it migrated to `~/.ai-hats/traits/personal-workflow/` and is
# activated per-user via the `--global` overlay. The TEMPORARY marker that
# pointed at "remove when user-skill-install lands" is now satisfied
# (HATS-421 shipped the user-skill-install mechanism).


# --- Existing-trait wiring (skill attachments) -----------------------------


def test_trait_agent_attaches_design_minimalism() -> None:
    trait = _load("packages/ai-hats-library/src/ai_hats_library/core/traits/trait-agent/config.yaml")
    assert "design-minimalism" in trait.composition.skills


def test_trait_se_mindset_attaches_predictive_accounting() -> None:
    trait = _load("packages/ai-hats-library/src/ai_hats_library/usage/traits/trait-se-mindset/config.yaml")
    assert "predictive-accounting" in trait.composition.skills


# --- Initial-wizard wiring -------------------------------------------------


def test_initial_wizard_includes_ai_hats_framework_trait() -> None:
    role = _load("packages/ai-hats-library/src/ai_hats_library/core/roles/initial-wizard/config.yaml")
    assert "ai-hats-framework" in role.composition.traits


# --- Rule extensions (existing rules, new content) -------------------------


def test_rule_backlog_discipline_has_hyp_vs_technical_risk() -> None:
    body = _read("packages/ai-hats-library/src/ai_hats_library/core/rules/rule_backlog_discipline/rule.md")
    assert "HYP vs technical risk" in body
    assert "agent-behaviour" in body or "agent-behavior" in body


def test_scope_guard_has_skeleton_signal_bullet() -> None:
    body = _read("packages/ai-hats-library/src/ai_hats_library/core/skills/scope-guard/SKILL.md")
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


# --- HATS-392 — assistant split + dev-python role -------------------------


def test_assistant_has_expected_traits() -> None:
    """HATS-433: personal-workflow removed. HATS-510 (5a): integration::google
    dropped (opinionated default did not fit non-Google users) → 6 traits."""
    role = _load("packages/ai-hats-library/src/ai_hats_library/usage/roles/assistant/config.yaml")
    expected = {
        "trait-base",
        "trait-agent",
        "trait-se-mindset",
        "trait-researcher-mindset",
        "dev::python",
        "dev::shell",
    }
    assert set(role.composition.traits) == expected


def test_assistant_drops_repo_specific_traits() -> None:
    role = _load("packages/ai-hats-library/src/ai_hats_library/usage/roles/assistant/config.yaml")
    forbidden = {"skill-engineer", "ai-hats-maintainer"}
    assert not (forbidden & set(role.composition.traits))


def test_dev_python_role_exists() -> None:
    role = _load("packages/ai-hats-library/src/ai_hats_library/usage/roles/dev-python/config.yaml")
    assert role.name == "dev-python"


def test_dev_python_composition() -> None:
    role = _load("packages/ai-hats-library/src/ai_hats_library/usage/roles/dev-python/config.yaml")
    expected = {
        "trait-base",
        "trait-agent",
        "trait-se-mindset",
        "trait-researcher-mindset",
        "dev::python",
        "dev::shell",
    }
    assert set(role.composition.traits) == expected


def test_dev_python_is_clean_no_google_integration() -> None:
    """HATS-433: personal-workflow removed from library entirely — this
    test now only guards against integration::google leaking into the
    clean Python baseline (assistant has it; dev-python must not)."""
    role = _load("packages/ai-hats-library/src/ai_hats_library/usage/roles/dev-python/config.yaml")
    assert "integration::google" not in role.composition.traits


def test_dev_python_injection_has_role_header() -> None:
    role = _load("packages/ai-hats-library/src/ai_hats_library/usage/roles/dev-python/config.yaml")
    assert "PYTHON DEVELOPMENT ASSISTANT" in role.injection


def test_initial_wizard_recommends_dev_python_for_pyproject() -> None:
    body = _read("packages/ai-hats-library/src/ai_hats_library/core/roles/initial-wizard/config.yaml")
    # Step 3 stack-detection mapping must route pyproject.toml to dev-python.
    assert "pyproject.toml" in body
    # The exact bullet that defines the mapping after HATS-392.
    assert "**dev-python**" in body


def test_initial_wizard_uses_live_role_catalog_placeholder() -> None:
    # HATS-625: the wizard no longer hand-maintains a role list (it drifted —
    # missed dev-web, role-curator, project roles). The Available-roles section
    # now injects the LIVE catalog via the <available_roles> placeholder,
    # expanded at prompt-build time. The "dev-python is surfaced" runtime
    # guarantee moved to tests/test_role_catalog.py.
    body = _read("packages/ai-hats-library/src/ai_hats_library/core/roles/initial-wizard/config.yaml")
    assert "<available_roles>" in body, "wizard must inject the live role catalog"
    assert "## Available base roles" not in body, "the baked role list must be gone"


def test_initial_wizard_recommends_dev_python_for_setup_py() -> None:
    # Step 3 mapping must cover both pyproject.toml AND setup.py.
    body = _read("packages/ai-hats-library/src/ai_hats_library/core/roles/initial-wizard/config.yaml")
    # Find the line that maps setup.py — must point to dev-python.
    lines = [ln for ln in body.splitlines() if "setup.py" in ln and "→" in ln]
    assert lines, "no setup.py mapping line found in initial-wizard"
    assert any("dev-python" in ln for ln in lines), (
        f"setup.py mapping does not target dev-python: {lines}"
    )


@pytest.mark.parametrize(
    "doc_rel",
    [
        "README.md",
        "docs/glossary.md",
        "docs/ARCHITECTURE.md",
        "docs/how-to-extend.md",
        "docs/how-to-configure.md",
    ],
)
def test_doc_catalog_mentions_dev_python(doc_rel: str) -> None:
    body = _read(doc_rel)
    assert "dev-python" in body, f"{doc_rel} must mention dev-python in its catalog/mapping"
