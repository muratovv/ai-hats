"""opencode on the plan path reports the session the builder path reports
(ADR-0036 D5) — the oracle for the planner, until the builder path is gone."""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.assembler import Assembler
from ai_hats.dry_run import DRY_RUN_SESSION_ID, dry_run_automate, dry_run_hitl
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import RunMode, SessionPolicy, assemble_brief
from ai_hats.session_plan import preview


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """A composable project on a synthetic library, with the person's config
    home pinned under ``HOME`` and no opencode directory in it yet."""
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("AI_HATS_OPENCODE_CONFIG_HOME", "XDG_CONFIG_HOME"):
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
    ProjectConfig(provider="opencode", library_paths=[str(lib)]).save(proj / PROJECT_CONFIG)
    asm = Assembler(proj, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="opencode")
    return proj


def _populate(home: Path) -> None:
    """A lived-in home: a config to link and a skill of the person's own."""
    opencode = home / ".config" / "opencode"
    (opencode / "skills" / "personal").mkdir(parents=True)
    (opencode / "skills" / "personal" / "SKILL.md").write_text("personal")
    (opencode / "opencode.json").write_text('{"theme": "dark"}')


@pytest.mark.parametrize("populated", [False, True], ids=["empty-home", "lived-in-home"])
@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_the_plan_path_reports_the_session_the_builder_path_reports(
    project: Path, tmp_path: Path, run_mode: RunMode, populated: bool
):
    """Same launch, environment, prompt, consent and composition; the plan
    writes the skill documents it always writes and names the root it creates."""
    if populated:
        _populate(tmp_path / "home")
    layout = ProjectLayout.at(project)
    if run_mode is RunMode.HITL:
        old = dry_run_hitl(layout, role="test-role", provider="opencode", policy=SessionPolicy())
        brief = None
    else:
        old = dry_run_automate(
            layout, role="test-role", provider="opencode", task="demo", policy=SessionPolicy()
        )
        brief = assemble_brief(layout, task="demo", ticket_id="")
    shown = preview(layout, role="test-role", provider="opencode", run_mode=run_mode, brief=brief)
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
    assert "--agent" in new["launch"]
    [config] = [e for e in new["materialized"] if e["kind"] == "merge_json"]
    assert config["target"].endswith("/opencode/opencode.json")
    assert "## AVAILABLE SKILLS" in shown.prompt and "/skills/s/SKILL.md" in shown.prompt

    old_rows = {(e["kind"], e["target"], e["digest"]) for e in old_record["materialized"]}
    new_rows = {(e["kind"], e["target"], e["digest"]) for e in new["materialized"]}
    assert old_rows <= new_rows
    names = {target.rsplit("/", 1)[-1] for _kind, target, _digest in new_rows - old_rows}
    # The root: the builder recorded its mkdir only when nothing under it came
    # first (a port overlay), the real launch always created it — the plan names it.
    assert names == {"SKILL.md", DRY_RUN_SESSION_ID}
    assert set(old_record) - set(new) == {"duplicates", "escapes"}
    linked = {t.rsplit("/", 1)[-1] for k, t, _d in new_rows if k == "symlink"}
    assert linked == ({"opencode.json", "personal"} if populated else set())
