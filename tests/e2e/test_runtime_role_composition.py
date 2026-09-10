"""e2e (HATS-1456)

flow:   a user composes a role at runtime with `+` / `-` instead of editing
        ai-hats.yaml, then inspects or runs the result
cmds:
    ai-hats self init -r assistant -p claude --no-update   # the precondition
    ai-hats config show-prompt -r "assistant + ai-hats-framework"
    ai-hats config show-prompt -r "assistant - trait-se-mindset"
    ai-hats config show-prompt -r "assistant+ai-hats-framework"    # compact form
    ai-hats agent "assistant + ai-hats-framework" --task hello --dry-run --json
    ai-hats --dry-run-json -r "assistant + ai-hats-framework"
    ai-hats config set -r "assistant + ai-hats-framework"          # must refuse
    ai-hats -r assistant + ai-hats-framework                       # no-resolve: bare +, must refuse
expect: an added trait's injection appears in the prompt, a removed one
        disappears while its siblings stay, and compact and spaced spellings are
        byte-identical; the composed prompt is measurably larger than the base
        through both `agent --dry-run --json` and `--dry-run-json`; an unknown
        component, a role in second position, and a bare unquoted `+` each exit 2
        with a named error and no traceback; `config set` refuses to persist and
        leaves ai-hats.yaml byte-identical
why:    composition is the surface where a wrong answer is silent — a trait that
        fails to attach still yields a working prompt, just not the one asked
        for, so only comparing prompts catches it
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.library]


def test_e2e_runtime_composition_add(tmp_project):
    """Scenario 1: adding a trait via runtime composition includes its prompt injection."""
    result = tmp_project.run(
        "config", "show-prompt", "-r", "assistant + ai-hats-framework", timeout=10.0
    )
    assert result.exit_code == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "## AI-HATS FRAMEWORK" in result.stdout


def test_e2e_runtime_composition_remove(tmp_project):
    """Scenario 2: removing a trait excludes its prompt injection while keeping others."""
    result = tmp_project.run(
        "config", "show-prompt", "-r", "assistant - trait-se-mindset", timeout=10.0
    )
    assert result.exit_code == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "## SE MINDSET" not in result.stdout
    assert "## RESEARCHER MINDSET" in result.stdout


def test_e2e_runtime_composition_compact_syntax(tmp_project):
    """Scenario 3: compact form 'assistant+trait' is byte-for-byte identical to spaced form."""
    res_spaced = tmp_project.run(
        "config", "show-prompt", "-r", "assistant + ai-hats-framework", timeout=10.0
    )
    res_compact = tmp_project.run(
        "config", "show-prompt", "-r", "assistant+ai-hats-framework", timeout=10.0
    )
    assert res_spaced.exit_code == 0
    assert res_compact.exit_code == 0
    assert res_spaced.stdout == res_compact.stdout


def test_e2e_runtime_composition_unknown_component_error(tmp_project):
    """Scenario 4: unknown component name exits with code 2 and a friendly error."""
    result = tmp_project.run(
        "config", "show-prompt", "-r", "assistant + non-existent-trait", timeout=10.0
    )
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
    result = tmp_project.run(
        "config", "show-prompt", "-r", "assistant - global_rule_resource_hygiene", timeout=10.0
    )
    assert result.exit_code == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "# Global Rule: Resource Hygiene" not in result.stdout


def test_e2e_runtime_composition_unquoted_plus_refused(tmp_project):
    """Scenario 8 (S5b): bare '+' in passthrough arguments is rejected with a helpful error."""
    result = tmp_project.run("-r", "assistant", "+", "ai-hats-framework", timeout=10.0)
    assert result.exit_code == 2, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "bare '+' in provider arguments" in result.stderr


def test_e2e_runtime_composition_agent_subcommand(tmp_project):
    """Scenario 9 (N3): 'ai-hats agent' subcommand accepts role spec and alters prompt size."""
    res_base = tmp_project.run(
        "agent", "assistant", "--task", "hello", "--dry-run", "--json", timeout=10.0
    )
    res_spec = tmp_project.run(
        "agent",
        "assistant + ai-hats-framework",
        "--task",
        "hello",
        "--dry-run",
        "--json",
        timeout=10.0,
    )
    assert res_base.exit_code == 0, f"base failed: {res_base.stderr}"
    assert res_spec.exit_code == 0, f"spec failed: {res_spec.stderr}"
    import json

    data_base = json.loads(res_base.stdout)
    data_spec = json.loads(res_spec.stdout)
    s_base = next(
        m["size"] for m in data_base["materialized"] if m["target"] == data_base["prompt"]
    )
    s_spec = next(
        m["size"] for m in data_spec["materialized"] if m["target"] == data_spec["prompt"]
    )
    assert s_spec > s_base, f"spec size {s_spec} should be > base size {s_base}"


def test_e2e_runtime_composition_dry_run_json(tmp_project):
    """Scenario 10 (M1): --dry-run-json with runtime spec succeeds and alters delivered prompt size.

    Note: audit snapshot (which carries provenance=="runtime") is generated at session
    creation in build_composition_payload (covered in-process by test_composition_seam.py),
    while --dry-run-json goes through build_preview_payload.
    """
    res_base = tmp_project.run("--dry-run-json", "-r", "assistant", timeout=10.0)
    res_spec = tmp_project.run(
        "--dry-run-json", "-r", "assistant + ai-hats-framework", timeout=10.0
    )
    assert res_base.exit_code == 0, f"base failed: {res_base.stderr}"
    assert res_spec.exit_code == 0, f"spec failed: {res_spec.stderr}"
    import json

    data_base = json.loads(res_base.stdout)
    data_spec = json.loads(res_spec.stdout)
    s_base = next(
        m["size"] for m in data_base["materialized"] if m["target"] == data_base["prompt"]
    )
    s_spec = next(
        m["size"] for m in data_spec["materialized"] if m["target"] == data_spec["prompt"]
    )
    assert s_spec > s_base, f"spec size {s_spec} should be > base size {s_base}"
