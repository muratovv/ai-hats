"""HATS-1479: the sub-agent meta-prompt names its project directory.

`assemble_meta_prompt` carried role + ticket + task and never said WHERE the
project is. Claude survived that because the CLI process cwd is the project
dir; a surface whose tool picks its own cwd (agy) does not, and resolves the
project to whatever absolute path the prompt happens to name — in a measured
run, the live checkout quoted inside the observed session's evidence.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path

from ai_hats.session_artifacts import assemble_meta_prompt


def test_meta_prompt_names_the_project_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    out = assemble_meta_prompt(
        ProjectLayout.at(project_dir), role_context="# SYSTEM_ROLE\nstub", task="go", ticket_id=""
    )

    assert project_dir.resolve().as_posix() in out


def test_working_directory_precedes_the_task(tmp_path: Path) -> None:
    """The anchor is useless after the instructions that need it."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    out = assemble_meta_prompt(
        ProjectLayout.at(project_dir),
        role_context="# SYSTEM_ROLE\nstub",
        task="run rack ls",
        ticket_id="",
    )

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
