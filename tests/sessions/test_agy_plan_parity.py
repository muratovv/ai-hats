"""agy on the plan path reports the session the builder path reports
(ADR-0036 D5) — the oracle for the planner, until the builder path is gone."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.assembler import Assembler
from ai_hats.dry_run import dry_run_automate, dry_run_hitl
from ai_hats.materialization import render_json
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import RunMode, SessionPolicy, assemble_brief
from ai_hats.session_plan import preview
from ai_hats.surfaces.agy.global_hook import desired_hooks
from ai_hats.surfaces.agy.provider import agy_user_settings_json

PERSONAL_HOOK = {"matcher": "Edit", "command": "/opt/hooks/personal.sh"}


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """A composable project on a synthetic library, with the person's home
    pinned under ``HOME`` — the dispatcher registers in its settings file."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GEMINI_CONFIG_DIR", raising=False)

    lib = tmp_path / "lib"
    skill = lib / "skills" / "s"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: s\ndescription: x\n---\n# body\n")
    (skill / "scripts" / "tool.sh").write_text("#!/bin/sh\n")
    role = lib / "roles" / "test-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: test-role\npriorities:\n  - Quality\n"
        "composition:\n  skills: [s]\ninjection: Role body.\n"
    )

    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectConfig(provider="agy", library_paths=[str(lib)]).save(proj / PROJECT_CONFIG)
    asm = Assembler(proj, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="agy")
    return proj


def _populate() -> Path:
    """A lived-in home: a settings file with the person's own hook row."""
    settings = agy_user_settings_json()
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"model": "x", "hooks": {"PreToolUse": [PERSONAL_HOOK]}}, indent=2)
    )
    return settings


@pytest.mark.parametrize("populated", [False, True], ids=["empty-home", "lived-in-home"])
@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_the_plan_path_reports_the_session_the_builder_path_reports(
    project: Path, run_mode: RunMode, populated: bool
):
    """Same launch, environment, prompt, consent and composition; the plan
    writes the skill documents it always writes, and names the dispatcher
    registration as the patch it is rather than the document it results in."""
    if populated:
        _populate()
    layout = ProjectLayout.at(project)
    if run_mode is RunMode.HITL:
        old = dry_run_hitl(layout, role="test-role", provider="agy", policy=SessionPolicy())
        brief = None
    else:
        old = dry_run_automate(
            layout, role="test-role", provider="agy", task="demo", policy=SessionPolicy()
        )
        brief = assemble_brief(layout, task="demo", ticket_id="")
    shown = preview(layout, role="test-role", provider="agy", run_mode=run_mode, brief=brief)
    old_record = old.to_dict()
    new = shown.record

    for key in (
        "role",
        "provider",
        "run_mode",
        "cwd",
        "policy",
        "env_keys",
        "prompt",
        "launch",
        "consent",
        "composition",
        "checks",
    ):
        assert new[key] == old_record[key], key
    assert shown.prompt == old.prompt_text
    if run_mode is RunMode.HITL:
        assert new["launch"] == ["agy", "--add-dir", str(layout.cache.session("dry-run") / "rules")]
        assert new["prompt"].endswith("/rules/GEMINI.md")
    else:
        assert new["prompt"] is None and new["launch"][-2] == "-p"

    old_rows = {(e["kind"], e["target"], e["digest"]) for e in old_record["materialized"]}
    new_rows = {(e["kind"], e["target"], e["digest"]) for e in new["materialized"]}
    settings = {row for row in old_rows if row[1] == str(agy_user_settings_json())}
    assert len(settings) == 1, "the builder registers the dispatcher on every launch"
    assert old_rows - settings <= new_rows
    [(kind, target, digest)] = settings
    [(new_kind, new_target, new_digest)] = [r for r in new_rows if r[1] == target]
    assert (new_kind, new_target) == ("merge_json", target)
    if populated:
        # The builder's `data` is the whole document it would leave behind (the
        # person's keys and rows included); the plan's is what ai-hats adds.
        assert new_digest != digest
    else:
        assert new_digest == digest, "an absent file: the patch IS the document"
    names = {t.rsplit("/", 1)[-1] for _k, t, _d in new_rows - old_rows}
    assert names == ({"SKILL.md", "settings.json"} if populated else {"SKILL.md"})
    assert set(old_record) - set(new) == {"duplicates", "escapes"}
    patch = next(e for e in new["materialized"] if e["kind"] == "merge_json")
    assert patch["size"] == len(render_json(desired_hooks()))
