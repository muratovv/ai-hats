"""The two shipped composites under both processings (HATS-1976 acceptance 2):
the candidate's sets of skills and prompt members equal the reference's on the
smallest role; a bare trait matches its own config; a planted extra shows."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from ai_hats.diagnostics import Diagnostic
from ai_hats.processing import Root, candidate_roots, compose

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
ROLE = "hypothesis-intake"


@pytest.fixture
def project(tmp_path: Path):
    """An empty project: no overlays of its own, the developer's home isolated
    by the suite, so both processings see the shipped library alone."""
    from ai_hats.assembler import Assembler

    proj = tmp_path / "proj"
    proj.mkdir()
    return Assembler(proj)


def _reference(asm, role: str):
    from ai_hats.materialize import compose_to_run
    from ai_hats.surfaces import adapt

    return adapt(
        compose_to_run(asm, role),
        identity=role,
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=(),
        diagnostics=[],
    )


def _candidate(asm, expression: str, roots=None):
    from ai_hats.paths import builtin_library_root

    diagnostics: list[Diagnostic] = []
    roots = roots or candidate_roots(
        asm.library_paths, builtin_root=builtin_library_root(asm.project_dir)
    )
    return compose(expression, roots, layout=asm.layout, diagnostics=diagnostics), diagnostics


def _members(plan) -> set[str]:
    return {m.name for b in plan.prompt.blocks for m in b.members}


def test_the_smallest_role_composes_the_same_sets_under_both(project):
    reference = _reference(project, ROLE)
    candidate, diagnostics = _candidate(project, ROLE)
    assert {s.name for s in candidate.skills} == {s.name for s in reference.skills}
    assert _members(candidate) == _members(reference)
    assert {s.name for s in reference.skills} == {"skills::safety-guard"}, "the sample's shape"
    assert diagnostics == []
    # Beyond the sets: the bytes of every member agree, so the plan comparison
    # of stage 2 will differ on order alone (ADR-0036 D9's declared divergence).
    assert {(m.name, m.text, m.heading) for b in candidate.prompt.blocks for m in b.members} == {
        (m.name, m.text, m.heading) for b in reference.prompt.blocks for m in b.members
    }
    assert [b.name for b in candidate.prompt.blocks] == [None, "PRIORITIES", "RULES"]
    assert [b.name for b in reference.prompt.blocks] == ["PRIORITIES", None, "RULES"]


def test_a_bare_trait_matches_its_own_config(project):
    config = yaml.safe_load((LIBRARY / "usage" / "traits" / "worker" / "config.yaml").read_text())
    assert config["composition"] == {"traits": [], "rules": [], "skills": []}
    candidate, _ = _candidate(project, "worker")
    assert _members(candidate) == {"worker::prompt"}
    assert candidate.skills == ()
    assert candidate.prompt.text == config["injection"].strip(), "as the composer hands it over"


def test_the_worker_comes_last_after_the_role(project):
    candidate, _ = _candidate(project, f"{ROLE} + worker")
    body = candidate.prompt.blocks[0]
    assert body.name is None
    assert [m.name for m in body.members][0] == f"{ROLE}::prompt"
    assert [m.name for m in body.members][-1] == "worker::prompt"


def test_a_planted_skill_in_the_copy_breaks_the_comparison(project, tmp_path: Path):
    """Positive control: the equality above is sensitive to one extra term."""
    planted = tmp_path / "planted"
    shutil.copytree(LIBRARY / "experimental", planted, symlinks=True)
    composite = planted / "core" / f"{ROLE}.yaml"
    composite.write_text(composite.read_text().replace("+ trait-base", "+ trait-base + worker"))
    roots = [Root(planted / "core"), Root(planted / "usage")]
    candidate, _ = _candidate(project, ROLE, roots=roots)
    reference = _reference(project, ROLE)
    assert _members(candidate) != _members(reference)
    assert _members(candidate) - _members(reference) == {"worker::prompt"}
