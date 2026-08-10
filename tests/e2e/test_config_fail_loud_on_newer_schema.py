"""e2e (HATS-792)

flow:   a developer attempts to run commands on a project whose config file was
        written by a future version of the tool
cmds:
    ai-hats config status
expect: process exits nonzero, prints "schema_version 99 is newer" with "ai-hats self
        update" remediation instructions, and leaves ai-hats.yaml byte-identical
why:    parsing a future schema version as a legacy format risks misinterpreting
        configuration fields or clobbering unrecognised options on save
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_e2e_newer_schema_version_fails_loud(tmp_project):
    yaml_path = tmp_project.yaml
    # Overwrite the bootstrapped config with a future-version one.
    future = (
        "schema_version: 99\nai_hats_dir: .agent/ai-hats\nprovider: claude\nfuture_field: keep-me\n"
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
    assert "ai-hats self update" in combined, f"missing remediation pointer; got:\n{combined}"

    # The future config is NOT silently rewritten — byte-for-byte intact.
    assert yaml_path.read_text() == future, f"future config was rewritten:\n{yaml_path.read_text()}"
