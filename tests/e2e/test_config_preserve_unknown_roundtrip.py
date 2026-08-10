"""e2e (HATS-792, HATS-581)

flow:   a developer mutates project options when the config file contains top-level
        fields added by another tool version
cmds:
    ai-hats config set --task-prefix ACME
expect: task_prefix is updated to ACME in ai-hats.yaml, a warning naming the unknown
        field appears on stderr, and the unrecognised field is retained on disk
why:    stripping unrecognised top-level keys on configuration save silently destroys
        settings written by newer or complementary tool versions
"""

from __future__ import annotations

import pytest
import yaml


@pytest.mark.integration
def test_e2e_unknown_top_level_field_survives_config_set(tmp_project):
    yaml_path = tmp_project.yaml
    # tmp_project already wrote a valid v4 ai-hats.yaml + bootstrapped .agent/.
    # Inject an unknown top-level field a NEWER ai-hats might have written.
    text = yaml_path.read_text()
    if not text.endswith("\n"):
        text += "\n"
    yaml_path.write_text(text + "future_field: keep-me\n")

    # A real command that LOADS and re-SAVES the config: change the task prefix.
    res = tmp_project.run("config", "set", "--task-prefix", "ACME")
    res.expect_ok()

    # The unknown field survived the rewrite (round-trip), AND the rewrite
    # actually happened (task_prefix landed).
    on_disk = yaml.safe_load(yaml_path.read_text())
    assert on_disk["future_field"] == "keep-me", (
        f"unknown top-level field dropped on save:\n{yaml_path.read_text()}"
    )
    assert on_disk["task_prefix"] == "ACME"

    # HATS-581 guard: the drop-WARN is still emitted on the load that the
    # command performs (preserve does not mean silence).
    assert "dropping unknown field 'future_field'" in res.stderr, (
        f"expected HATS-581 WARN on stderr; got:\n{res.stderr}"
    )
