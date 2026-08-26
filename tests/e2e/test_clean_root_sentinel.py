"""e2e (HATS-1338)

flow:   a developer running a session in a project workspace
cmds:
    ai-hats execute -r assistant
expect: project root remains clean with framework state kept strictly inside
        .agent/ai-hats/
why:    without root cleanliness guards, framework sessions pollute project roots with
        transient setting files
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.assembler import Assembler

from _helpers.env import checkout_pythonpath
from _helpers.hitl import drive_bare_hitl

pytestmark = pytest.mark.integration

_ALLOWLIST: set[str] = {
    ".gitignore",
    "ai-hats.yaml",
    "ai-hats.yaml.lock",
    ".agent",
    ".githooks",
}


def _root_entries(project_path: Path) -> set[str]:
    """Top-level entries in project root excluding .git."""
    return {p.name for p in project_path.iterdir() if p.name != ".git"}


def test_claude_clean_root_sentinel(
    tmp_venv_project,
    requires_claude_auth,
) -> None:
    """ADR-0021 M4 | GREEN-pin | Fail-under-revert: .claude/settings.json in root."""
    project = tmp_venv_project.path

    tmp_venv_project.run(
        "self", "init", "-r", "assistant", "-p", "claude", "--no-update", timeout=120
    ).expect_ok()

    after_init = _root_entries(project)

    # Positive control 1: init created .agent/ai-hats
    assert (project / ".agent" / "ai-hats").is_dir(), (
        f".agent/ai-hats was not created by init: {sorted(after_init)}"
    )

    result = (
        drive_bare_hitl(tmp_venv_project, role="assistant")
        .expect_no_hang()
        .expect_exit_in({0, 130})
        .expect_start_banner(role="assistant", provider="claude")
    )

    # Positive control 2: session actually ran
    assert result.duration_s > 0

    after_session = _root_entries(project)
    disallowed = after_session - _ALLOWLIST

    assert not disallowed, (
        f"project root contains unexpected entries after claude session: {sorted(disallowed)}"
    )


@pytest.mark.xfail(
    strict=True,
    reason="HATS-1338 — agy пишет ./GEMINI.md в корень проекта (assembler.py:772-776)",
)
def test_agy_clean_root_sentinel(
    tmp_venv_project,
    requires_agy_auth,
    repo_root: Path,
) -> None:
    """ADR-0021 M4 | RED-xfail | HATS-1338 will remove xfail when agy clean-root lands."""
    project = tmp_venv_project.path

    checkout_env = {"PYTHONPATH": checkout_pythonpath(repo_root)}

    tmp_venv_project.run(
        "self",
        "init",
        "-r",
        "assistant",
        "-p",
        "agy",
        "--no-update",
        timeout=120,
        extra_env=checkout_env,
    ).expect_ok()

    # Positive control: init created .agent/ai-hats
    assert (project / ".agent" / "ai-hats").is_dir()

    # set_role for agy provider triggers system prompt update (writing ./GEMINI.md into project root)
    Assembler(project).set_role("assistant", "agy")

    after_session = _root_entries(project)
    disallowed = after_session - _ALLOWLIST

    assert not disallowed, (
        f"project root contains unexpected entries after agy set_role: {sorted(disallowed)}"
    )
