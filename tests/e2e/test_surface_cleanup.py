"""E2E: HATS-407 — surface-command cleanup (init / config set / bump).

Validates the post-HATS-407 contracts via the **real** ``ai-hats`` binary
in real subprocess invocations:

- ``ai-hats self init`` — scaffolds dirs + ai-hats.yaml + ./CLAUDE.md +
  .gitignore + writes user-rules-only imports.md. **No** role-content
  materialization under ``<ai_hats_dir>/library/{rules,skills}/``,
  **no** ``.last_backup/`` created.

- ``ai-hats config set -r ROLE`` — yaml-only flip of ``default_role``.
  ``active_role`` stays empty (runtime cache, written by session start
  only). No mutation under ``<ai_hats_dir>/library/``, no ``.last_backup/``.

- ``ai-hats self bump`` — refreshes ``imports.md`` to pick up new
  user-rule files; no role-content materialization, no ``.last_backup/``.

Per ``dev_rule_e2e_gate``: real subprocess chain (real bash, real
``ai-hats`` binary already on PATH), ``@pytest.mark.integration``. No
pip install — we exercise the locally-installed binary directly so the
turnaround stays cheap.

Fail-under-revert (dev_rule_e2e_gate §4): reverting the HATS-407 commits
makes ``set_role`` repopulate ``library/rules/*`` and emit
``.last_backup/`` again; assertions below catch both regressions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _ai_hats_available() -> bool:
    return shutil.which("ai-hats") is not None


@pytest.fixture
def fresh_project(tmp_path):
    """Empty project dir + git init (so HATS-088 hook install path is exercisable)."""
    if not _ai_hats_available():
        pytest.skip("ai-hats binary not on PATH")
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(
        ["git", "init", "-q"],
        cwd=str(project), check=True, capture_output=True,
    )
    return project


def _run(cmd, *, cwd, expect_exit=0, timeout=60):
    env = os.environ.copy()
    # Avoid network / pip in the e2e: the binary on PATH is the one we
    # want under test.
    env["AI_HATS_NO_UPDATE"] = "1"
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


# --------------------------------------------------------------------- #
# 1. init — no role-content materialization
# --------------------------------------------------------------------- #


def test_init_writes_yaml_scaffold_and_user_rules_aggregator_only(fresh_project):
    """``ai-hats self init -p claude -r assistant --no-wizard --no-update``
    produces ai-hats.yaml + ./CLAUDE.md + imports.md aggregator. NO
    library/rules/* or library/skills/* materialization. NO .last_backup/.
    """
    project = fresh_project

    _run(
        ["ai-hats", "self", "init", "-p", "claude", "-r", "assistant",
         "--no-wizard", "--no-update"],
        cwd=project,
    )

    # ai-hats.yaml created with default_role + active_role split per HATS-407.
    yaml_path = project / "ai-hats.yaml"
    assert yaml_path.exists(), "ai-hats.yaml not created"
    body = yaml_path.read_text()
    assert "default_role: assistant" in body, body
    # active_role is the runtime cache — empty until first session_start.
    assert "active_role: ''" in body or "active_role: \"\"" in body, body

    # ./CLAUDE.md scaffold imports the canonical aggregator.
    claude_md = project / "CLAUDE.md"
    assert claude_md.exists(), "./CLAUDE.md scaffold not created"
    assert "@./.agent/ai-hats/imports.md" in claude_md.read_text()

    # imports.md exists — empty (no user-rules yet) or with newline.
    imports_md = project / ".agent" / "ai-hats" / "imports.md"
    assert imports_md.exists(), "imports.md aggregator not created"
    assert "@./" not in imports_md.read_text() or "user-rules" in imports_md.read_text()

    # NO role-content materialization (HATS-407 contract).
    canon = project / ".agent" / "ai-hats"
    for forbidden in ("priorities.md", "role.md", "skills_index.md"):
        assert not (canon / forbidden).exists(), f"{forbidden} should not be materialized"
    for forbidden_dir in ("traits", "rules"):
        assert not (canon / forbidden_dir).exists(), f"{forbidden_dir}/ should not be materialized"

    # NO _copy_components artefacts under library/rules or library/skills.
    library_rules = canon / "library" / "rules"
    library_skills = canon / "library" / "skills"
    for d in (library_rules, library_skills):
        if d.exists():
            # Dir may exist as an empty parent; no rule/skill subdirs.
            for child in d.iterdir():
                pytest.fail(f"unexpected materialization under {d}: {child.name}")

    # NO .last_backup/ created — HATS-407 dropped the backup chain.
    assert not (canon / ".last_backup").exists()

    # .gitignore has the framework-dir entry.
    gitignore = (project / ".gitignore").read_text()
    assert ".agent/ai-hats/" in gitignore


# --------------------------------------------------------------------- #
# 2. config set — yaml-only role flip
# --------------------------------------------------------------------- #


def test_config_set_role_is_yaml_only(fresh_project):
    """After init, ``ai-hats config set -r sre`` flips default_role
    in ai-hats.yaml without touching the canonical tree or .last_backup."""
    project = fresh_project
    _run(
        ["ai-hats", "self", "init", "-p", "claude", "-r", "assistant",
         "--no-wizard", "--no-update"],
        cwd=project,
    )

    canon = project / ".agent" / "ai-hats"
    # Snapshot canonical state immediately after init.
    initial_imports = (canon / "imports.md").read_text()

    res = _run(
        ["ai-hats", "config", "set", "-r", "sre"],
        cwd=project,
    )
    # CLI surfaces the new contract banner.
    assert "Default role" in res.stdout, res.stdout

    # default_role flipped; active_role stays empty (runtime cache).
    body = (project / "ai-hats.yaml").read_text()
    assert "default_role: sre" in body, body
    assert "active_role: ''" in body or "active_role: \"\"" in body, body

    # imports.md unchanged — config set must not regenerate.
    assert (canon / "imports.md").read_text() == initial_imports

    # No role-content materialization.
    for forbidden in ("priorities.md", "role.md", "skills_index.md"):
        assert not (canon / forbidden).exists()

    # No .last_backup/ — yaml-only path never creates one.
    assert not (canon / ".last_backup").exists()


# --------------------------------------------------------------------- #
# 3. bump — regenerates user-rules aggregator, no role-content
# --------------------------------------------------------------------- #


def test_bump_regenerates_user_rules_aggregator_only(fresh_project):
    """``ai-hats self bump`` after dropping a new user-rule must:
    - include the new rule in imports.md
    - NOT materialize role-content files
    - NOT create .last_backup/
    """
    project = fresh_project
    _run(
        ["ai-hats", "self", "init", "-p", "claude", "-r", "assistant",
         "--no-wizard", "--no-update"],
        cwd=project,
    )

    # Drop a user-rule.
    canon = project / ".agent" / "ai-hats"
    user_rules = canon / "user-rules"
    user_rules.mkdir(parents=True, exist_ok=True)
    (user_rules / "my-rule.md").write_text("# my rule\n\nProject-specific guidance.\n")

    # HATS-470: `self bump` CLI removed; direct bump testing routes
    # through the hidden `_bump_internal` entry-point (the same
    # subprocess hook `self update` uses internally). The bump banner
    # still prints "Bumped".
    import sys as _sys
    res = _run([_sys.executable, "-m", "ai_hats._bump_internal"], cwd=project)
    assert "Bumped" in res.stdout, res.stdout

    # imports.md picked up the new user-rule.
    imports_body = (canon / "imports.md").read_text()
    assert "@./user-rules/my-rule.md" in imports_body, imports_body

    # No role-content materialization (HATS-407 contract).
    for forbidden in ("priorities.md", "role.md", "skills_index.md"):
        assert not (canon / forbidden).exists()
    for forbidden_dir in ("traits",):
        assert not (canon / forbidden_dir).exists()

    # No .last_backup/.
    assert not (canon / ".last_backup").exists()


# --------------------------------------------------------------------- #
# 4. self rollback dropped — command should not exist anymore
# --------------------------------------------------------------------- #


def test_self_rollback_command_removed(fresh_project):
    """HATS-407: ``ai-hats self rollback`` was removed — invoking it
    must surface a 'no such command' error from click."""
    project = fresh_project
    _run(
        ["ai-hats", "self", "init", "-p", "claude", "-r", "assistant",
         "--no-wizard", "--no-update"],
        cwd=project,
    )

    res = _run(
        ["ai-hats", "self", "rollback"],
        cwd=project, expect_exit=None,
    )
    assert res.returncode != 0, "self rollback should not exist post-HATS-407"
    blob = (res.stdout + res.stderr).lower()
    assert "no such command" in blob or "rollback" in blob, blob
