"""e2e (HATS-1551)

flow:   an operator inspecting the session tree a role would get, on disk, without spawning
cmds:
    ai-hats --dry-run-json -r test-role
    ai-hats --dry-run-json --materialize -r test-role
    ai-hats agent test-role --task "e2e task" --json --dry-run --materialize
expect: a plain dry-run leaves both sid dirs absent; --materialize writes the tree under the
        fixed `dry-run-materialize` sid, reports its path in `notes`, and still shows no
        escapes; the AUTOMATE path writes the same tree.
why:    the flag can regress in either direction — --materialize silently writing nothing,
        leaving the operator inspecting an empty tree, or a plain --dry-run starting to
        write, so a read-only inspection mutates the cache a real launch then reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.dry_run import DRY_RUN_MATERIALIZE_SESSION_ID, DRY_RUN_SESSION_ID
from ai_hats_core.layout import ProjectLayout

pytestmark = [pytest.mark.integration]


def _seed_role(project_path: Path) -> None:
    lib = project_path / "libraries"
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\npriorities:\n  - Quality\ninjection: | \n  # Test Role\n"
    )


def test_e2e_dry_run_materialize_writes_tree_to_disk(tmp_project):
    _seed_role(tmp_project.path)

    cache_std = ProjectLayout.at(tmp_project.path).cache.session(DRY_RUN_SESSION_ID)
    cache_mat = ProjectLayout.at(tmp_project.path).cache.session(DRY_RUN_MATERIALIZE_SESSION_ID)

    # 1. Default --dry-run writes nothing
    res_default = tmp_project.run("--dry-run-json", "-r", "test-role").expect_ok()
    payload_default = json.loads(res_default.stdout)
    assert payload_default["escapes"] == []
    assert not cache_std.exists()
    assert not cache_mat.exists()

    # 2. --dry-run --materialize writes session tree to disk under dry-run-materialize sid
    res_mat = tmp_project.run("--dry-run-json", "--materialize", "-r", "test-role").expect_ok()
    payload_mat = json.loads(res_mat.stdout)
    assert payload_mat["escapes"] == []
    assert cache_mat.is_dir()
    files = [p for p in cache_mat.rglob("*") if p.is_file()]
    assert len(files) > 0
    assert any("materialized session tree written to disk at" in n for n in payload_mat["notes"])


def test_e2e_dry_run_automate_materialize_writes_tree_to_disk(tmp_project):
    _seed_role(tmp_project.path)

    cache_mat = ProjectLayout.at(tmp_project.path).cache.session(DRY_RUN_MATERIALIZE_SESSION_ID)

    res_mat = tmp_project.run(
        "agent", "test-role", "--task", "e2e task", "--json", "--dry-run", "--materialize"
    ).expect_ok()
    payload_mat = json.loads(res_mat.stdout)
    assert payload_mat["escapes"] == []
    assert cache_mat.is_dir()
    files = [p for p in cache_mat.rglob("*") if p.is_file()]
    assert len(files) > 0
    assert any("materialized session tree written to disk at" in n for n in payload_mat["notes"])
