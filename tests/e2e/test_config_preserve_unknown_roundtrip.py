"""E2E (HATS-792): a same-version unknown TOP-LEVEL field in ai-hats.yaml
survives a real CLI load→save (round-trip), and the HATS-581 WARN still fires.

POLICY (locked with reviewer): same-version unknown top-level fields are
PRESERVED, not dropped. Before HATS-792, ``_strip_unknown_fields`` popped the
key with a WARN but ``to_dict`` re-emitted only known fields, so any command
that re-saved the config (here ``config set``) silently lost the field.

Per ``dev_rule_e2e_gate`` this is a real-binary test: real ``ai-hats`` process
(the dev-venv binary via the ``tmp_project`` fixture, worktree-portable),
marked ``integration``.

Fail-under-revert: drop the ``_extra`` capture in ``from_yaml`` (or the merge in
``to_dict``) and the ``future_field`` assertion after ``config set`` fails — the
field is gone from the rewritten yaml. Drop the WARN and the stderr assertion
fails.
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
