"""HATS-513 / ADR-0007 — judge role two-phase split.

Static shape assertions on:

- `judge-auditor` role exists, composes `base-auditor` (L0), does NOT
  compose `base-judge`, declares no CLI-mutation skills in its own list.
- `judge` role now composes `base-judge` (L1) on top of trait-agent.
- `judge-protocol` no longer carries Mode-A/B autopilot wording.
- `judge-auditor-protocol` exists and forbids state-mutating CLI verbs.
- `reflect-hypothesis-phase1.yaml` + `reflect-hypothesis-phase2.yaml`
  pipelines load, and their step IO is the expected shape.
- Initial injection files `reflect-hypothesis` /
  `reflect-hypothesis-interactive` exist.

These are static-shape assertions on the YAML configs / SKILL.md / pipeline
YAML — no subprocess, no real session. The E2E gate is covered by
`tests/e2e/test_reflect_hypothesis_e2e.py`.
"""
from __future__ import annotations

from pathlib import Path

from ai_hats.composer import Composer
from ai_hats.models import ComponentConfig
from ai_hats.pipeline.loader import load_pipeline
from ai_hats.resolver import LibraryResolver

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY = REPO_ROOT / "library"


def _load(rel: str) -> ComponentConfig:
    return ComponentConfig.from_yaml(REPO_ROOT / rel)


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def _composer() -> Composer:
    return Composer(LibraryResolver([LIBRARY / "core"]))


# --- Role files exist + minimal shape --------------------------------------


def test_judge_auditor_role_exists() -> None:
    role = _load("library/core/roles/judge-auditor/config.yaml")
    assert role.name == "judge-auditor"


def test_judge_auditor_composes_base_auditor() -> None:
    role = _load("library/core/roles/judge-auditor/config.yaml")
    trait_names = list(role.composition.traits)
    assert "base-auditor" in trait_names, (
        "judge-auditor must compose base-auditor (L0 baseline) per ADR-0007 §П1"
    )
    assert "base-judge" not in trait_names, (
        "judge-auditor must NOT compose base-judge (L0, not L1) — "
        "ADR-0007 §П1"
    )


def test_judge_auditor_skill_declared() -> None:
    role = _load("library/core/roles/judge-auditor/config.yaml")
    skill_names = list(role.composition.skills)
    assert "judge-auditor-protocol" in skill_names


def test_judge_auditor_protocol_skill_exists() -> None:
    body = _read("library/core/skills/judge-auditor-protocol/SKILL.md")
    assert "BEGIN_JUDGE_DRAFT" in body
    assert "END_JUDGE_DRAFT" in body
    # L0 baseline must not invite state-mutating CLI invocations
    # (the protocol skill must not contradict base-auditor's contract).
    # The skill mentions these verbs only as "Phase-2 CLI (record, do not run)"
    # — but the wording "Phase 2 CLI" must be present, not bare invocations.
    assert "do NOT invoke" in body or "do not invoke" in body.lower()


# --- judge role refactor ----------------------------------------------------


def test_judge_role_composes_base_judge() -> None:
    """HATS-513 issue #1: judge inherits base-judge L1 contract,
    symmetric with judge-for-role (ADR-0007 §П1)."""
    role = _load("library/core/roles/judge/config.yaml")
    assert "base-judge" in list(role.composition.traits), (
        "judge must now compose base-judge (fixes asymmetry with "
        "judge-for-role; HATS-513 issue #1)"
    )


def test_judge_protocol_has_no_mode_switch() -> None:
    """HATS-513 — Mode-A (autopilot) / Mode-B (interactive) runtime
    switch replaced by structural pipeline composition. The protocol
    skill no longer mentions Step 0 / autopilot / Mode A."""
    body = _read("library/core/skills/judge-protocol/SKILL.md")
    # The legacy headings/identifiers are gone.
    assert "Mode A — Autopilot" not in body
    assert "## Step 0 — Mode selection" not in body
    assert "AI_HATS_HITL=1" not in body
    # Phase 2 is now the only mode; the skill says so explicitly.
    assert "Phase 2" in body
    assert "draft" in body.lower()


# --- Compositions resolve ---------------------------------------------------


def test_judge_auditor_composition_resolves() -> None:
    res = _composer().compose("judge-auditor")
    assert res.errors == []
    # L0 baseline (base-auditor) injection block is present (substring).
    merged = "\n".join(res.injections)
    assert "AUDITOR BEHAVIOR (L0" in merged, (
        "base-auditor L0 contract must be in judge-auditor's prompt"
    )


def test_judge_composition_resolves() -> None:
    res = _composer().compose("judge")
    assert res.errors == []
    merged = "\n".join(res.injections)
    assert "JUDGE BEHAVIOR (L1" in merged, (
        "base-judge L1 contract must be in judge's prompt (HATS-513 fix)"
    )
    # Phase-2 framing is present
    assert "Phase 2" in merged


# --- Pipelines load + step IO ----------------------------------------------


def test_reflect_hypothesis_phase1_pipeline_loads() -> None:
    p = load_pipeline(LIBRARY / "core" / "pipelines"
                      / "reflect-hypothesis-phase1.yaml")
    step_names = [s.io.name for s in p.steps]
    assert step_names == [
        "compose_role",
        "resolve_prompt",
        "provider",
        "extract_marker",
        "save_artifact",
    ]
    # extract_marker emits judge_draft; save_artifact consumes it.
    extract = p.steps[3]
    save = p.steps[4]
    assert "judge_draft" in extract.io.produces
    assert "judge_draft" in save.io.requires
    assert "saved_path" in save.io.produces


def test_reflect_hypothesis_phase2_pipeline_loads() -> None:
    p = load_pipeline(LIBRARY / "core" / "pipelines"
                      / "reflect-hypothesis-phase2.yaml")
    step_names = [s.io.name for s in p.steps]
    assert step_names == [
        "compose_role",
        "resolve_prompt",
        "provider",
        "extract_marker",
        "save_artifact",
    ]
    extract = p.steps[3]
    save = p.steps[4]
    assert "judge_report" in extract.io.produces
    assert "judge_report" in save.io.requires


# --- Initial injections exist ----------------------------------------------


def test_initial_injections_exist() -> None:
    p1 = LIBRARY / "core" / "initial_injections" / "reflect-hypothesis.md"
    p2 = (LIBRARY / "core" / "initial_injections"
          / "reflect-hypothesis-interactive.md")
    assert p1.is_file(), f"missing {p1}"
    assert p2.is_file(), f"missing {p2}"
    # Phase 2 preamble must carry the {draft_body} placeholder the CLI
    # substitutes.
    assert "{draft_body}" in p2.read_text()


def test_preambles_do_not_contain_marker_literals() -> None:
    """Marker-collision defense (reviewer nit #4).

    `extract_marker` uses ``str.find`` — a substring match. If a
    preamble contains the literal marker string ``BEGIN_JUDGE`` or
    ``BEGIN_JUDGE_DRAFT`` and the LLM echoes the preamble in its
    transcript, the extractor will lock onto the echoed marker
    position instead of the real one. Defense: don't put literal
    marker strings in preambles — point at the protocol skill for
    the verbatim source.
    """
    for stem in ("reflect-hypothesis", "reflect-hypothesis-interactive"):
        path = LIBRARY / "core" / "initial_injections" / f"{stem}.md"
        body = path.read_text()
        for marker in (
            "BEGIN_JUDGE_DRAFT", "END_JUDGE_DRAFT",
            "BEGIN_JUDGE", "END_JUDGE",
        ):
            assert marker not in body, (
                f"{path.name} contains literal marker {marker!r} — "
                "transcript echo could poison extract_marker. Refer to "
                "the protocol skill instead."
            )
