"""The ``-re`` dispatch in the compose seam (HATS-1976 fork 2): the preview
payload comes from processing over ``experimental/`` and carries no composer
value; everything right of the plan is the path the reference takes."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.composition_seam import (
    build_composition_payload,
    build_preview_payload,
    compose_for_carry,
    compose_for_checks,
)
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.processing import ProcessingError
from ai_hats.session_artifacts import RunMode
from ai_hats.session_plan import preview
from tests._flat_library import write_root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project whose local ``libraries/experimental/`` is the sample root."""
    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(proj / PROJECT_CONFIG)
    write_root(proj / "libraries" / "experimental")
    return proj


def test_the_candidate_payload_carries_the_plan_and_no_composer_value(project: Path):
    payload = build_preview_payload(project, role="alpha + bare", experimental=True)
    assert payload.result is None
    assert payload.plan is not None
    assert payload.plan.identity == "alpha + bare"
    assert payload.effective_role == payload.role_expression == "alpha + bare"
    assert payload.provider.name == "claude"
    assert payload.layout is not None and payload.layout.root == project
    assert [m.name for b in payload.plan.prompt.blocks for m in b.members] == [
        "alpha::prompt",
        "shared::prompt",
        "bare::prompt",
        "alpha::priorities",
        "rules::rule-one",
    ]
    assert [d.text for d in payload.diagnostics if "README" not in str(d.where)] == []


def test_the_launch_payload_is_the_candidate_plan_and_leaves_the_config(project: Path):
    """A session from an expression must not persist that expression as the project's role."""
    before = (project / PROJECT_CONFIG).read_text()
    payload = build_composition_payload(
        project,
        role_override="  alpha   +   bare ",
        provider_name="claude",
        interactive=True,
        experimental=True,
    )

    assert payload.result is None
    assert payload.plan is not None
    assert payload.plan.identity == "alpha + bare"
    assert payload.plan.dsl == "re"
    assert payload.plan.hooks.runtime == () and payload.plan.hooks.external == ()
    assert payload.effective_role == payload.role_expression == "alpha + bare"
    assert payload.session_factory is not None
    assert payload.audit_writer_factory is not None
    assert payload.transcript_resolver is not None
    assert (project / PROJECT_CONFIG).read_text() == before


def test_an_empty_candidate_says_so_and_one_hook_does_not():
    from types import SimpleNamespace

    from ai_hats.startup_notices import candidate_session_notice

    empty = SimpleNamespace(runtime=(), external=())
    notice = candidate_session_notice("re", empty)
    assert notice is not None and notice.level == "note"
    assert notice.text == "candidate session: 0 hooks, 0 consent points"
    hooked = SimpleNamespace(runtime=(), external=(object(),))
    assert candidate_session_notice("re", hooked) is None
    assert candidate_session_notice("", empty) is None


def test_the_marker_keeps_the_channels_off_the_composer(project: Path):
    """Under re the expression is a plan. Under the empty marker it is a role name, and this one is not."""
    assert compose_for_checks(project, "alpha + bare", dsl="re") is None
    assert compose_for_carry(project, "alpha + bare", dsl="re") is None
    with pytest.raises(Exception, match="not a known trait"):
        compose_for_checks(project, "alpha + bare", dsl="")


def test_the_candidate_needs_an_expression(project: Path):
    with pytest.raises(ProcessingError, match="needs an expression"):
        build_preview_payload(project, role=None, experimental=True)


def test_a_project_without_a_candidate_library_says_where_it_comes_from(
    tmp_path: Path, monkeypatch
):
    proj = tmp_path / "bare"
    proj.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(proj / PROJECT_CONFIG)
    # A builtin root that serves the manifest but no experimental/ copy.
    builtin = tmp_path / "builtin"
    (builtin / "core" / "pipelines").mkdir(parents=True)
    (builtin / "usage").mkdir()
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(builtin))
    with pytest.raises(ProcessingError, match="no experimental/ under any library root"):
        build_preview_payload(proj, role="alpha", experimental=True)


def test_a_processing_refusal_surfaces_as_is(project: Path):
    with pytest.raises(ProcessingError, match="unknown name 'nope'"):
        build_preview_payload(project, role="alpha + nope", experimental=True)


def test_the_preview_projects_the_candidate_plan(project: Path):
    from ai_hats.assembler import Assembler

    shown = preview(
        Assembler(project).layout,
        role="alpha + bare",
        provider="claude",
        run_mode=RunMode.HITL,
        experimental=True,
    )
    assert shown.record["role"] == "alpha + bare"
    assert shown.record["composition"]["identity"] == "alpha + bare"
    assert shown.record["composition"]["hooks"] == {"runtime": [], "external": []}
    assert shown.record["consent"] == [] and shown.record["checks"] == []
    assert shown.prompt.startswith("<!-- AI-HATS:START -->\nALPHA\n"), "the surface's own frame"
    assert "BARE\n" in shown.prompt and "## PRIORITIES" in shown.prompt


def test_compose_role_reads_the_plan_when_there_is_no_composer_value(project: Path):
    from ai_hats.pipeline.steps.compose import ComposeRole

    payload = build_composition_payload(project, role_override="alpha", experimental=True)
    out = ComposeRole().run(composition=payload)
    assert out["system_prompt"].startswith("ALPHA\n")


def test_the_prompt_step_prices_a_payload_without_a_composer_value(project: Path):
    from ai_hats.pipeline.steps.materialize import MaterializeSystemPrompt

    payload = build_preview_payload(project, role="alpha", experimental=True)
    out = MaterializeSystemPrompt().run(composition=payload)
    assert out["system_prompt_text"].startswith("ALPHA\n")
    stats = out["composition_stats"]
    assert stats["role"] == "alpha" and stats["skill_count"] == 1
    assert stats["prompt_members"][0] == "alpha::prompt"
