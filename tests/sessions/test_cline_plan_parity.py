"""cline on the plan path reports the session the builder path reports
(ADR-0036 D5) — the oracle for the planner, until the builder path is gone."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.assembler import Assembler
from ai_hats.dry_run import dry_run_automate, dry_run_hitl
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import RunMode, SessionPolicy, assemble_brief
from ai_hats.session_plan import preview
from ai_hats.surfaces.cline.runtime_hooks import HOOK_SHIMS, shim_source

_PLAIN_SKILL_MD = "---\nname: s\ndescription: x\n---\n# body\n"
_HOOKED_SKILL_MD = (
    "---\n"
    "name: s\n"
    "description: x\n"
    "ai_hats:\n"
    "  runtime_hooks:\n"
    "    PreToolUse:\n"
    "      - matcher: Bash\n"
    "        script: hooks/guard.sh\n"
    "---\n"
    "# body\n"
)


@pytest.fixture(params=[False, True], ids=["plain", "hooked"])
def project(tmp_path: Path, monkeypatch, request) -> tuple[Path, bool]:
    """A composable project on a synthetic library, the person's home pinned;
    the hooked variant's skill declares one runtime hook with a script to run."""
    hooked: bool = request.param
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLINE_DATA_DIR", raising=False)

    lib = tmp_path / "lib"
    skill = lib / "skills" / "s"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text(_HOOKED_SKILL_MD if hooked else _PLAIN_SKILL_MD)
    (skill / "scripts" / "tool.sh").write_text("#!/bin/sh\n")
    if hooked:
        (skill / "hooks").mkdir()
        guard = skill / "hooks" / "guard.sh"
        guard.write_text("#!/bin/sh\nexit 0\n")
        guard.chmod(0o755)
    role = lib / "roles" / "test-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: test-role\npriorities:\n  - Quality\n"
        "composition:\n  skills: [s]\ninjection: Role body.\n"
    )

    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectConfig(provider="cline", library_paths=[str(lib)]).save(proj / PROJECT_CONFIG)
    asm = Assembler(proj, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="cline")
    return proj, hooked


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_the_plan_path_reports_the_session_the_builder_path_reports(
    project: tuple[Path, bool], run_mode: RunMode
):
    """Same launch, environment, prompt, consent and composition; the plan
    writes the skill documents it always writes, and names the hook shims as
    the two scripts they are rather than a copy of the package directory."""
    proj, hooked = project
    layout = ProjectLayout.at(proj)
    if run_mode is RunMode.HITL:
        old = dry_run_hitl(layout, role="test-role", provider="cline", policy=SessionPolicy())
        brief = None
    else:
        old = dry_run_automate(
            layout, role="test-role", provider="cline", task="demo", policy=SessionPolicy()
        )
        brief = assemble_brief(layout, task="demo", ticket_id="")
    shown = preview(layout, role="test-role", provider="cline", run_mode=run_mode, brief=brief)
    old_record = old.to_dict()
    new = shown.record
    root = layout.cache.session("dry-run")

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
    assert new["prompt"] is None, "the role rides the argv, no context file is written"
    if run_mode is RunMode.HITL:
        assert new["launch"][:3] == ["cline", "-i", "-s"]
        assert new["launch"][3] == shown.prompt
    else:
        assert new["launch"][-3:-1] == ["--yolo", "--json"]
        assert new["launch"][-1].startswith(shown.prompt)
    assert ("--hooks-dir" in new["launch"]) is hooked
    assert ("AI_HATS_SESSION_CACHE_DIR" in new["env_keys"]) is hooked

    old_rows = {(e["kind"], e["target"], e["digest"]) for e in old_record["materialized"]}
    new_rows = {(e["kind"], e["target"], e["digest"]) for e in new["materialized"]}
    # The builder copied the package's hooks directory as one tree; the plan
    # writes the two shims it holds, with the bytes of the package files.
    shims = {
        ("write_executable", str(root / "hooks" / name), _sha256(shim_source(name)))
        for name in HOOK_SHIMS
    }
    old_only = old_rows - new_rows
    if hooked:
        [(kind, target, _digest)] = old_only
        assert (kind, target) == ("copy_tree", str(root / "hooks"))
        assert shims <= new_rows
    else:
        assert old_only == set()
        assert not any(kind == "write_executable" for kind, _t, _d in new_rows)
    names = {t.rsplit("/", 1)[-1] for _k, t, _d in new_rows - old_rows}
    assert names == ({"SKILL.md", *HOOK_SHIMS} if hooked else {"SKILL.md"})
    assert set(old_record) - set(new) == {"duplicates", "escapes"}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
