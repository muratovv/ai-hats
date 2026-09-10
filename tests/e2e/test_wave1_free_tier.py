"""e2e (HATS-478)

flow:   a developer running free-tier inspection commands in a role-less project
cmds:
    ai-hats list providers
    ai-hats list roles
    ai-hats config show-prompt
expect: free-tier CLI commands execute under 5s without launching provider sessions or
        burning API
        quota
why: without free-tier command validation, simple inspection subcommands require
     expensive provider
        initializations"""

from __future__ import annotations

import pytest

# HATS-746: real-subprocess tests (tmp_project spawns the real ai-hats binary).
# Without this, the pre-push gate's `-m "(integration or smoke) and not
# quarantine"` selection deselects the file — it survived only by accident in
# CI Job 1's `not integration` pool.
pytestmark = [pytest.mark.integration, pytest.mark.library]


def test_list_providers_includes_claude(tmp_project) -> None:
    """``ai-hats list providers`` enumerates the builtin provider (claude).

    agy/cline are out-of-tree entry-point surfaces, absent from the free-tier
    checkout PYTHONPATH — only the builtin shows here.
    """
    tmp_project.run("list", "providers").expect_ok().expect_stdout_contains(
        "claude",
    )


def test_list_roles_shows_bundled_defaults(tmp_project) -> None:
    """``ai-hats list roles`` falls back to bundled library when
    ``library_paths`` is empty — the well-known defaults must surface."""
    tmp_project.run("list", "roles").expect_ok().expect_stdout_contains(
        "assistant",
        "architect",
    )


def test_config_show_prompt_reports_no_active_role(tmp_project) -> None:
    """No role set in an initialised project → non-zero exit and a
    stable marker on stdout explaining the gap."""
    tmp_project.run("config", "show-prompt").expect_failure().expect_stdout_contains(
        "no role to materialize",
    )
