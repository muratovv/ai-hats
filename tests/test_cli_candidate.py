"""The ``-re`` root flag and its two projections, in process (ADR-0034 D5):
``dry-run`` and ``show-prompt`` read the candidate plan; without the flag they
name the reference spelling; the reference flags are untouched."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from tests._flat_library import write_root


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(proj / PROJECT_CONFIG)
    write_root(proj / "libraries" / "experimental")
    monkeypatch.chdir(proj)
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")
    return proj


def test_dry_run_json_projects_the_candidate_record(project: Path):
    result = CliRunner().invoke(main, ["-re", "dry-run", "alpha + bare", "--json"])
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["role"] == "alpha + bare"
    assert record["composition"]["identity"] == "alpha + bare"
    assert [m["name"] for m in record["composition"]["prompt"]["blocks"][0]["members"]] == [
        "alpha::prompt",
        "shared::prompt",
        "bare::prompt",
    ]
    assert [s["name"] for s in record["composition"]["skills"]] == ["skills::tool"]
    assert record["composition"]["hooks"] == {"runtime": [], "external": []}


def test_dry_run_text_and_json_are_one_record(project: Path):
    text = CliRunner().invoke(main, ["-re", "dry-run", "alpha"])
    assert text.exit_code == 0, text.output
    assert text.output.startswith("role      alpha -> claude   run_mode=hitl\n")
    assert "checks\n  (none bound)" in text.output
    full = CliRunner().invoke(main, ["-re", "dry-run", "alpha", "--full"])
    assert full.exit_code == 0, full.output
    assert "ALPHA\n" in full.output and "ALPHA\n" not in text.output


def test_show_prompt_prints_the_prompt_bytes(project: Path):
    result = CliRunner().invoke(main, ["-re", "show-prompt", "alpha + bare"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("ALPHA\n"), "the plan's prompt.text, as config show-prompt"
    assert "BARE\n" in result.output and "## RULES" in result.output


def test_a_processing_refusal_renders_friendly(project: Path):
    for args in (["dry-run", "alpha + nope"], ["show-prompt", "alpha - nope"]):
        result = CliRunner().invoke(main, ["-re", *args])
        assert result.exit_code == 2, result.output
        assert "Error: unknown name 'nope'" in result.output
        assert "Traceback" not in result.output


def test_a_processing_warning_reaches_the_notes_and_stderr(project: Path):
    runner = CliRunner()
    shown = runner.invoke(main, ["-re", "dry-run", "tool + alpha", "--json"])
    assert shown.exit_code == 0, shown.output
    notes = json.loads(shown.output)["notes"]
    assert any("first body prompt, alpha::prompt, comes through term 2" in n for n in notes)
    printed = runner.invoke(main, ["-re", "show-prompt", "tool + alpha"])
    assert printed.exit_code == 0, printed.output
    assert "WARN: the first body prompt, alpha::prompt, comes through term 2" in printed.output


def test_the_projections_refuse_without_the_flag():
    runner = CliRunner()
    result = runner.invoke(main, ["dry-run", "alpha"])
    assert result.exit_code == 2
    assert "runs under -re" in result.output and "ai-hats --dry-run -r <role>" in result.output
    result = runner.invoke(main, ["show-prompt", "alpha"])
    assert result.exit_code == 2
    assert "ai-hats config show-prompt --role <role>" in result.output


def test_a_bare_flag_dry_run_is_the_candidate_record(project: Path):
    result = CliRunner().invoke(main, ["-re", "--dry-run-json", "alpha + bare"])
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["composition"]["identity"] == "alpha + bare"
    assert record["composition"]["dsl"] == "re"
    assert record["composition"]["hooks"] == {"runtime": [], "external": []}


def test_re_combined_with_a_role_is_refused(project: Path):
    result = CliRunner().invoke(main, ["-re", "-r", "alpha"])
    assert result.exit_code == 2, result.output
    assert "-re" in result.output and "-r" in result.output


def test_re_without_an_expression_names_the_forms(project: Path):
    result = CliRunner().invoke(main, ["-re"])
    assert result.exit_code == 2, result.output
    assert 'ai-hats -re "<expr>"' in result.output
    assert "dry-run" in result.output


def test_agent_dry_run_under_re_matches_the_hitl_record(project: Path):
    runner = CliRunner()
    hitl = runner.invoke(main, ["-re", "dry-run", "alpha + bare", "--json"])
    agent = runner.invoke(main, ["-re", "agent", "alpha + bare", "--dry-run", "--json"])
    assert hitl.exit_code == 0, hitl.output
    assert agent.exit_code == 0, agent.output
    assert json.loads(hitl.output)["composition"] == json.loads(agent.output)["composition"]


def test_list_tokens_under_re_prices_the_candidate_plan(project: Path):
    from ai_hats.composition_seam import build_preview_payload
    from ai_hats.costs import analyze_plan

    plan = build_preview_payload(project, role="alpha", experimental=True).plan
    total = f"{analyze_plan(plan, exact=False).total_tokens:,}"
    result = CliRunner().invoke(main, ["-re", "list", "tokens", "alpha", "--approx"])
    assert result.exit_code == 0, result.output
    assert total in result.output


def test_the_reference_role_flag_is_untouched(project: Path):
    """``-r`` still reads a role name, and only the exact ``-re`` is the flag:
    ``-repo`` is ``-r epo``, refused as a role by the reference path."""
    for argv, role in (
        (["-r", "nope", "--dry-run"], "nope"),
        (["-rnope", "--dry-run"], "nope"),
        (["--role", "nope", "--dry-run"], "nope"),
        (["-repo", "--dry-run"], "epo"),
    ):
        result = CliRunner().invoke(main, argv)
        assert result.exit_code == 2, result.output
        assert f"Role '{role}' not found" in result.output, result.output
    assert "-re" in CliRunner().invoke(main, ["--help"]).output
