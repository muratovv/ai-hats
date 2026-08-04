"""E2E tests for runtime role composition (HATS-1456).

Covers CLI surfaces with expressions in -r ("maintainer + trait", "maintainer - trait"),
verifying prompt output, error handling (exit 2, no tracebacks), syntax equivalence,
and persistence prevention.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]


def test_e2e_runtime_composition_add(tmp_project):
    """Scenario 1: adding a trait via runtime composition includes its prompt injection."""
    result = tmp_project.run("config", "show-prompt", "-r", "assistant + ai-hats-framework", timeout=10.0)
    assert result.exit_code == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "## AI-HATS FRAMEWORK" in result.stdout


def test_e2e_runtime_composition_remove(tmp_project):
    """Scenario 2: removing a trait excludes its prompt injection while keeping others."""
    result = tmp_project.run("config", "show-prompt", "-r", "assistant - trait-se-mindset", timeout=10.0)
    assert result.exit_code == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "## SE MINDSET" not in result.stdout
    assert "## RESEARCHER MINDSET" in result.stdout


def test_e2e_runtime_composition_compact_syntax(tmp_project):
    """Scenario 3: compact form 'assistant+trait' is byte-for-byte identical to spaced form."""
    res_spaced = tmp_project.run("config", "show-prompt", "-r", "assistant + ai-hats-framework", timeout=10.0)
    res_compact = tmp_project.run("config", "show-prompt", "-r", "assistant+ai-hats-framework", timeout=10.0)
    assert res_spaced.exit_code == 0
    assert res_compact.exit_code == 0
    assert res_spaced.stdout == res_compact.stdout


def test_e2e_runtime_composition_unknown_component_error(tmp_project):
    """Scenario 4: unknown component name exits with code 2 and a friendly error."""
    result = tmp_project.run("config", "show-prompt", "-r", "assistant + non-existent-trait", timeout=10.0)
    assert result.exit_code == 2, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "not a known trait, rule or skill" in result.stderr
    assert "Traceback" not in (result.stdout + result.stderr)


def test_e2e_runtime_composition_role_in_second_position(tmp_project):
    """Scenario 5: role in second position exits with code 2."""
    result = tmp_project.run("config", "show-prompt", "-r", "assistant + architect", timeout=10.0)
    assert result.exit_code == 2, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "'architect' is a role" in result.stderr
    assert "Traceback" not in (result.stdout + result.stderr)


def test_e2e_runtime_composition_persisting_refused(tmp_project):
    """Scenario 6: attempting to persist runtime composition is refused and leaves config untouched."""
    cfg_file = tmp_project.path / "ai-hats.yaml"
    before_content = cfg_file.read_text()

    result = tmp_project.run("config", "set", "-r", "assistant + ai-hats-framework", timeout=10.0)
    assert result.exit_code != 0
    assert "runtime composition ('+' / '-') cannot be persisted" in result.stdout + result.stderr
    assert cfg_file.read_text() == before_content


def test_e2e_runtime_composition_remove_rule_from_trait(tmp_project):
    """Scenario 7 (S2b): removing a rule brought by a trait works cleanly through the CLI."""
    result = tmp_project.run("config", "show-prompt", "-r", "assistant - global_rule_resource_hygiene", timeout=10.0)
    assert result.exit_code == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "# Global Rule: Resource Hygiene" not in result.stdout


def test_e2e_runtime_composition_unquoted_plus_refused(tmp_project):
    """Scenario 8 (S5b): bare '+' in passthrough arguments is rejected with a helpful error."""
    result = tmp_project.run("-r", "assistant", "+", "ai-hats-framework", timeout=10.0)
    assert result.exit_code == 2, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "bare '+' in provider arguments" in result.stderr


def test_e2e_runtime_composition_agent_subcommand(tmp_project):
    """Scenario 9 (M1): 'ai-hats agent' subcommand accepts role spec expressions."""
    result = tmp_project.run("agent", "assistant + ai-hats-framework", "--task", "hello", "--dry-run", timeout=10.0)
    assert result.exit_code == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_e2e_runtime_composition_dry_run_json(tmp_project):
    """Scenario 10 (M1): --dry-run-json includes composition snapshot with runtime overlay info."""
    result = tmp_project.run("--dry-run-json", "-r", "assistant + ai-hats-framework", timeout=10.0)
    assert result.exit_code == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert '"role"' in result.stdout


