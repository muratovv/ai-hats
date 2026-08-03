"""HATS-1479: the sub-agent meta-prompt names its project directory.

`_build_meta_prompt` carried role + ticket + task and never said WHERE the
project is. Claude survived that because the CLI process cwd is the project
dir; a surface whose tool picks its own cwd (agy) does not, and resolves the
project to whatever absolute path the prompt happens to name — in a measured
run, the live checkout quoted inside the observed session's evidence.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.paths import runs_dir
from ai_hats.runtime import SubAgentRunner
from ai_hats_observe import SessionManager


def _null_payload():
    """Minimal CompositionPayload for helper-method seams (HATS-865)."""
    from ai_hats.composition_payload import CompositionPayload
    from ai_hats_core import CompositionResult

    return CompositionPayload(
        result=CompositionResult(
            name="t", priorities=[], rules=[], skills=[], injections=[]
        ),
        provider=None,
        effective_role="t",
    )


def _runner(project_dir: Path) -> SubAgentRunner:
    session_mgr = SessionManager(project_dir, runs_dir=runs_dir(project_dir))
    return SubAgentRunner(project_dir, _null_payload(), session_mgr=session_mgr)


def test_meta_prompt_names_the_project_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    out = _runner(project_dir)._build_meta_prompt(
        role_context="# SYSTEM_ROLE\nstub", task="go", ticket_id=""
    )

    assert project_dir.resolve().as_posix() in out


def test_working_directory_precedes_the_task(tmp_path: Path) -> None:
    """The anchor is useless after the instructions that need it."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    out = _runner(project_dir)._build_meta_prompt(
        role_context="# SYSTEM_ROLE\nstub", task="run rack ls", ticket_id=""
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

    expanded = expand_path_placeholders(role, tmp_path)
    assert "<project_dir>" not in expanded
    assert tmp_path.resolve().as_posix() in expanded
