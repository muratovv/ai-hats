"""codex on the plan path reports the session the builder path reports
(ADR-0036 D5) — the oracle for the planner, until the builder path is gone."""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.assembler import Assembler
from ai_hats.dry_run import dry_run_automate, dry_run_hitl
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import RunMode, SessionPolicy, assemble_brief
from ai_hats.session_plan import preview


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """A composable project on a synthetic library, with the person's Codex
    home pinned under ``HOME`` — the surface refuses a base home that is not there."""
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("AI_HATS_CODEX_BASE_HOME", "CODEX_HOME", "CODEX_SQLITE_HOME"):
        monkeypatch.delenv(name, raising=False)

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
    ProjectConfig(provider="codex", library_paths=[str(lib)]).save(proj / PROJECT_CONFIG)
    asm = Assembler(proj, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="codex")
    return proj


def _populate(home: Path) -> None:
    """A lived-in home: a config to link, a skill of the person's own, a
    file login, and sqlite state that must stay behind."""
    codex = home / ".codex"
    (codex / "config.toml").write_text('model = "x"\n')
    (codex / "skills" / "personal").mkdir(parents=True)
    (codex / "skills" / "personal" / "SKILL.md").write_text("personal")
    (codex / "auth.json").write_text('{"token": "t"}')
    (codex / "state_5.sqlite").write_text("db")


@pytest.mark.parametrize("populated", [False, True], ids=["empty-home", "lived-in-home"])
@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_the_plan_path_reports_the_session_the_builder_path_reports(
    project: Path, tmp_path: Path, run_mode: RunMode, populated: bool
):
    """Same launch, environment, prompt, consent and composition; the plan
    writes the skill documents it always writes and stages the credential as
    the copy it is."""
    if populated:
        _populate(tmp_path / "home")
    layout = ProjectLayout.at(project)
    if run_mode is RunMode.HITL:
        old = dry_run_hitl(layout, role="test-role", provider="codex", policy=SessionPolicy())
        brief = None
    else:
        old = dry_run_automate(
            layout, role="test-role", provider="codex", task="demo", policy=SessionPolicy()
        )
        brief = assemble_brief(layout, task="demo", ticket_id="")
    shown = preview(layout, role="test-role", provider="codex", run_mode=run_mode, brief=brief)
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
    developer = next(t for t in new["launch"] if t.startswith("developer_instructions="))
    assert "## AVAILABLE SKILLS" in developer and "/skills/s/SKILL.md" in developer

    old_rows = {(e["kind"], e["target"], e["digest"]) for e in old_record["materialized"]}
    new_rows = {(e["kind"], e["target"], e["digest"]) for e in new["materialized"]}
    staged = {row for row in old_rows if row[1].endswith("/auth.json")}
    assert old_rows - staged <= new_rows
    assert {("copy_file", target, None) for _kind, target, _digest in staged} <= new_rows
    assert len(staged) == (1 if populated else 0)
    names = {target.rsplit("/", 1)[-1] for _kind, target, _digest in new_rows - old_rows}
    assert names == ({"SKILL.md", "auth.json"} if populated else {"SKILL.md"})
    assert set(old_record) - set(new) == {"duplicates", "escapes"}
    if populated:
        linked = {t.rsplit("/", 1)[-1] for k, t, _d in new_rows if k == "symlink"}
        assert linked == {"config.toml", "personal"}, "sqlite state and auth are never linked"
