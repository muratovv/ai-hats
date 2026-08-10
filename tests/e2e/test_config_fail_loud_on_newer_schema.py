"""e2e (HATS-792)

flow:   a user whose ai-hats.yaml specifies a schema_version newer than the installed binary runs any ai-hats command
cmds:
    ai-hats config status
expect: the command fails with a nonzero exit code, displays a schema error and remediation update pointer, and leaves the configuration file un-rewritten on disk
why:    silently parsing a future schema as a legacy version could misread fields or corrupt future config options on save
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
