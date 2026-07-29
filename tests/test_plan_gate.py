"""plan-gate skill ↔ engine section-catalog sync (HATS-636).

The skill's section→skill table must list exactly the engine's catalog, in
order — otherwise the documented gate contract drifts from the enforced one.
`SKILL.md` names this exact path, so keep the file here; the catalog's own unit
tests live in `packages/ai-hats-rack/tests/test_plan_extensions.py`.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats_rack.extensions.sections import DEFAULT_PLAN_SECTIONS

# --- plan-gate orchestrator ↔ engine sync (HATS-636) ----------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_GATE_SKILL = (
    REPO_ROOT
    / "packages"
    / "ai-hats-library"
    / "src"
    / "ai_hats_library"
    / "core"
    / "skills"
    / "plan-gate"
    / "SKILL.md"
)


def _plan_gate_section_names() -> list[str]:
    """First-column entries of the plan-gate section→skill markdown table."""
    names: list[str] = []
    in_table = False
    for line in PLAN_GATE_SKILL.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("| Plan section"):
            in_table = True
            continue
        if not in_table:
            continue
        if not stripped.startswith("|"):
            break  # table ended
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or set(cells[0]) <= set("-: "):
            continue  # separator row (|---|---|)
        names.append(cells[0])
    return names


def test_plan_gate_skill_table_matches_engine_sections() -> None:
    engine = [s.name for s in DEFAULT_PLAN_SECTIONS]
    assert engine, "positive control: DEFAULT_PLAN_SECTIONS must be non-empty"

    skill = _plan_gate_section_names()
    assert skill, (
        "parser found no rows in the plan-gate section table — check the "
        f"table header/format in {PLAN_GATE_SKILL}"
    )
    assert skill == engine, (
        "plan-gate SKILL.md section table drifted from the engine section "
        "catalog (ai_hats_rack.extensions.sections.DEFAULT_PLAN_SECTIONS).\n"
        f"  engine: {engine}\n  skill:  {skill}"
    )
