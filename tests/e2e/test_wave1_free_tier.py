"""Wave 1 free-tier pilots — prove ``tmp_project`` fixture works.

These three tests exercise the role-less :func:`tmp_project` fixture
against CLI commands that don't require an active agent (free-tier:
no SDK calls, $0 quota, <5s wall-clock target). Each maps to a Core
scenario from the HATS-466 scenarios catalog:

* ``test_list_providers_includes_claude_and_gemini`` → S-CLI-22
* ``test_list_roles_shows_bundled_defaults`` → S-CLI-23 (adjusted:
  empty ``library_paths`` falls back to the framework-bundled library,
  so the stable assertion is "the well-known defaults are visible",
  not "no roles enumerated")
* ``test_config_show_prompt_reports_no_active_role`` → S-CLI-16
  negative shape (no role set in an initialised project → non-zero
  exit + stable error marker on stdout)

Body of each test stays ≤ 10 LOC — if a pilot grows past that, the
fixture is doing too little.
"""

from __future__ import annotations


def test_list_providers_includes_claude_and_gemini(tmp_project) -> None:
    """``ai-hats list providers`` enumerates the two bundled providers."""
    tmp_project.run("list", "providers").expect_ok().expect_stdout_contains(
        "claude", "gemini",
    )


def test_list_roles_shows_bundled_defaults(tmp_project) -> None:
    """``ai-hats list roles`` falls back to bundled library when
    ``library_paths`` is empty — the well-known defaults must surface."""
    tmp_project.run("list", "roles").expect_ok().expect_stdout_contains(
        "assistant", "architect",
    )


def test_config_show_prompt_reports_no_active_role(tmp_project) -> None:
    """No role set in an initialised project → non-zero exit and a
    stable marker on stdout explaining the gap."""
    tmp_project.run("config", "show-prompt").expect_failure().expect_stdout_contains(
        "no role to materialize",
    )
