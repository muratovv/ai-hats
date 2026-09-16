"""HATS-1479: the sub-agent meta-prompt names its project directory.

The meta-prompt carried role + ticket + task and never said WHERE the
project is. Claude survived that because the CLI process cwd is the project
dir; a surface whose tool picks its own cwd (agy) does not, and resolves the
project to whatever absolute path the prompt happens to name — in a measured
run, the live checkout quoted inside the observed session's evidence.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path

from ai_hats.materialization import describe_write_text
from ai_hats.session_artifacts import RunMode, SessionPolicy, working_directory_section
from ai_hats.surfaces.cline.provider import ClineSurface
from ai_hats.surfaces.plan import Launch, MaterializationPlan
from tests._plan_helpers import composition_with, flags


def _meta_prompt(project_dir: Path, *, task: str) -> str:
    """The one token a CLI surface's sub-agent launch hands its harness."""
    layout = ProjectLayout.at(project_dir)
    root = project_dir.parent / "sessions" / "s"
    composition = composition_with("r", prompt="# SYSTEM_ROLE\nstub\n")
    plan = MaterializationPlan(
        composition=composition,
        prompt=composition.prompt,
        surface="cline",
        run_mode=RunMode.AUTOMATE,
        policy=SessionPolicy(),
        root=root,
        entries=(describe_write_text(root / "context.md", "# SYSTEM_ROLE\nstub"),),
        env={},
        launch=Launch(args=(), sdk_options=None),
        context=root / "context.md",
    )
    launched = ClineSurface().automate_launch(
        plan, flags(root, brief=f"# TASK\n{task}"), {}, layout=layout
    )
    return launched.prompt


def test_meta_prompt_names_the_project_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    out = _meta_prompt(project_dir, task="go")

    assert project_dir.resolve().as_posix() in out
    assert working_directory_section(ProjectLayout.at(project_dir)) in out


def test_working_directory_precedes_the_task(tmp_path: Path) -> None:
    """The anchor is useless after the instructions that need it."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    out = _meta_prompt(project_dir, task="run rack ls")

    assert out.index(project_dir.resolve().as_posix()) < out.index("# TASK")


def test_session_reviewer_role_anchors_cli_calls_and_expands(tmp_path: Path) -> None:
    """The role uses `<project_dir>`, and no surface ships the literal token.

    HATS-380 is the failure this guards: an unexpanded placeholder gets obeyed
    verbatim. Both surfaces expand, so the assertion is on the shared function.
    """
    import ai_hats_library

    from ai_hats.placeholders import expand_path_placeholders

    role = (
        Path(ai_hats_library.__file__).parent
        / "core"
        / "roles"
        / "session-reviewer"
        / "config.yaml"
    ).read_text()

    assert "<project_dir>" in role, "role lost its absolute project anchor"
    # Positive control: if this vanishes too, the file/path is wrong, not the role.
    assert "review-session" in role

    expanded = expand_path_placeholders(role, ProjectLayout.at(tmp_path))
    assert "<project_dir>" not in expanded
    assert tmp_path.resolve().as_posix() in expanded
