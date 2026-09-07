"""e2e (HATS-1894)

flow:   a user whose ai-hats.yaml carries a value the schema refuses runs a
        command that needs the project
cmds:
    ai-hats wt list
expect: a rendered refusal naming the file and the field, exit 2, no traceback
why:    ProjectConfigError was the one resolution error missing from the
        friendly-error registry, so a one-character typo in ai-hats.yaml dumped
        pydantic's stack instead of the message the family already produces
"""
# comment-length: allow — the four-field catalog block, schema in gen_e2e_catalog.py

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_bad_project_config_refuses_typed(tmp_project):
    """`config status` and `self update` degrade over this file instead (they
    resolve leniently); every other command refuses, and this is that refusal."""
    (tmp_project.path / "ai-hats.yaml").write_text(
        "provider: claude\nmanage_gitignore: not-a-bool\n"
    )

    res = tmp_project.run("wt", "list").expect_exit(2)

    assert "Traceback" not in res.output, res.output
    res.expect_output_contains("ai-hats.yaml", "manage_gitignore")
