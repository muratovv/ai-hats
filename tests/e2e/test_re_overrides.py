"""e2e (HATS-2011)

flow:   a maintainer adds an override in the project's candidate library and
        reads it back from dry-run, show-prompt and list; a leading minus over
        active drops a skill the bare role still carries
cmds:
    ai-hats -re dry-run maintainer --json
    ai-hats -re show-prompt maintainer
    ai-hats -re list overrides
    ai-hats -re dry-run "- devils-advocate" --json
expect: the project override is brought_by in the trace and its text follows the
        role prose; list prints the override; over active the identity is the
        canonical sum and the subtracted skill is gone
why:    an override is the only way a project adds to a role it does not own,
        and a leading sign is how one machine takes one skill off active
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.library]


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_a_project_override_is_visible_in_both_projections(tmp_project):
    lib = tmp_project.path / "libraries" / "experimental"
    _write(
        lib,
        "note.md",
        "---\nschema_version: 1\ntype: prompt\nblock: body\n---\nPROJECT NOTE\n",
    )
    _write(
        lib,
        "arm-maintainer.yaml",
        'schema_version: 1\ntype: override\napply: "^maintainer$"\nexpr: "+ note"\n',
    )
    _write(
        lib,
        "role.yaml",
        "schema_version: 1\ntype: composite\nexpr: kept + dropped\ntags: [role]\n",
    )
    _write(lib, "kept.md", "---\nschema_version: 1\ntype: prompt\nblock: body\n---\nKEPT\n")
    _write(lib, "dropped.md", "---\nschema_version: 1\ntype: prompt\nblock: body\n---\nDROPPED\n")
    _write(
        lib,
        "cut-role.yaml",
        'schema_version: 1\ntype: override\napply: "^role$"\nexpr: "- dropped"\n',
    )

    listed = tmp_project.run("-re", "list", "overrides").expect_ok()
    assert "arm-maintainer" in listed.stdout
    assert "cut-role" in listed.stdout

    bare = tmp_project.run("-re", "show-prompt", "worker").expect_ok()
    assert "PROJECT NOTE" not in bare.stdout

    shown = tmp_project.run("-re", "show-prompt", "maintainer").expect_ok()
    assert shown.stdout.index("AI-HATS MAINTAINER") < shown.stdout.index("PROJECT NOTE")

    record = json.loads(
        tmp_project.run("-re", "dry-run", "maintainer", "--json").expect_ok().stdout
    )
    brought = {row["term"]: row["brought_by"] for row in record["composition"]["trace"]}
    assert brought["note"] == "arm-maintainer"

    cut = json.loads(tmp_project.run("-re", "dry-run", "role", "--json").expect_ok().stdout)
    removed = {
        row["term"]: row["removed_by"] for row in cut["composition"]["trace"] if row["removed_by"]
    }
    assert removed["dropped"] == "cut-role"


def test_a_leading_sign_over_active_drops_the_skill(tmp_project):
    local = tmp_project.path / ".agent" / "ai-hats"
    local.mkdir(parents=True, exist_ok=True)
    (local / "local.yaml").write_text("active: maintainer\n")

    full = json.loads(tmp_project.run("-re", "dry-run", "maintainer", "--json").expect_ok().stdout)
    skills = {skill["name"] for skill in full["composition"]["skills"]}
    assert any(name.endswith("devils-advocate") for name in skills)

    cut = json.loads(
        tmp_project.run("-re", "dry-run", "--json", "--", "- devils-advocate").expect_ok().stdout
    )
    assert cut["composition"]["identity"] == "maintainer - devils-advocate"
    left = {skill["name"] for skill in cut["composition"]["skills"]}
    assert not any(name.endswith("devils-advocate") for name in left)
    tmp_project.run("-re", "show-prompt", "--", "- devils-advocate").expect_ok()
