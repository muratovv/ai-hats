"""E2E (HATS-792): an ai-hats.yaml whose ``schema_version`` is newer than this
binary understands makes a real ``ai-hats`` command FAIL LOUD — nonzero exit +
remediation pointer — and does NOT silently rewrite the file.

POLICY (locked with reviewer): ``schema_version > KNOWN_SCHEMA_VERSION`` →
refuse to operate. Migrations run upward only to the known version; silently
treating a newer schema as the known one would both misread its format and risk
clobbering future fields on the next save. So ``from_yaml`` raises, and any
command that loads the config inherits the nonzero exit.

Per ``dev_rule_e2e_gate`` this is a real-binary test: real ``ai-hats`` process
(the dev-venv binary via ``tmp_project``), marked ``integration``.

Fail-under-revert: remove the schema_version guard in ``from_yaml`` →
schema_version 99 loads silently (treated as v4), the command exits 0, and the
exit/remediation/no-rewrite assertions below fail.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_e2e_newer_schema_version_fails_loud(tmp_project):
    yaml_path = tmp_project.yaml
    # Overwrite the bootstrapped config with a future-version one.
    future = (
        "schema_version: 99\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "provider: claude\n"
        "future_field: keep-me\n"
    )
    yaml_path.write_text(future)

    # A real command that loads the project config (assembler → from_yaml).
    res = tmp_project.run("config", "status")

    # Refuse to operate: nonzero exit + remediation pointer somewhere on the
    # process output (the error propagates as a ProjectConfigError traceback /
    # message; the remediation string is the load-bearing part).
    res.expect_failure()
    combined = res.stdout + res.stderr
    assert "schema_version 99 is newer" in combined, (
        f"missing schema-too-new diagnostic; got:\n{combined}"
    )
    assert "ai-hats self update" in combined, (
        f"missing remediation pointer; got:\n{combined}"
    )

    # The future config is NOT silently rewritten — byte-for-byte intact.
    assert yaml_path.read_text() == future, (
        f"future config was rewritten:\n{yaml_path.read_text()}"
    )
