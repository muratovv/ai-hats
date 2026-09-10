"""e2e (HATS-337, HATS-407, HATS-790, HATS-1203)

flow:   a developer initializing project configuration
cmds:
    ai-hats self init -p claude -r assistant --no-wizard
expect: project configuration creates ai-hats.yaml and user-rules/ without copying
        role content files or creating legacy backup directories
why:    project initialization must create clean minimal configuration files without
        materializing redundant framework copies

flow:   a developer updating default role configuration
cmds:
    ai-hats config set -r sre
expect: default_role is updated in ai-hats.yaml without modifying the canonical
        framework directory or creating backups
why:    config set must perform yaml-only role configuration updates
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from ai_hats.paths import PROJECT_CONFIG


pytestmark = [pytest.mark.integration, pytest.mark.library, pytest.mark.surfaces]


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# HATS-790 (Alt 5): there is no ``<venv>/bin/ai-hats`` console script anymore.
# Invoke the package via ``<dev-venv python> -m ai_hats`` — resolved through
# ``sys.executable`` so it works from both the main checkout and from linked git
# worktrees (where ``<worktree>/.venv`` does not exist). Bypassing the host
# launcher is intentional — see module docstring (HATS-337/339/552).
AI_HATS = [sys.executable, "-m", "ai_hats"]


def _ai_hats_available() -> bool:
    """True iff the dev-venv interpreter can import the package (HATS-790:
    importability replaces the removed bin/ai-hats existence check).

    Carries HATS-1218's foreign-checkout guard too: this module builds its own
    ``AI_HATS`` rather than taking the ``ai_hats_shim`` fixture, so it is the
    one e2e file the conftest guard never sees. Unguarded it goes green on
    another checkout's install — silently, which is worse than not running.
    """
    import os

    from _helpers.env import clean_env
    from _helpers.interpreter import (
        foreign_source_checkout,
        remedy,
        resolve_ai_hats_init,
    )

    resolved = resolve_ai_hats_init(clean_env(os.environ))
    if resolved is None:
        return False
    foreign = foreign_source_checkout(resolved, REPO_ROOT)
    if foreign is not None:
        pytest.fail(remedy(REPO_ROOT, foreign), pytrace=False)
    return True


@pytest.fixture
def fresh_project(tmp_path):
    """Empty project dir + git init (so HATS-088 hook install path is exercisable)."""
    if not _ai_hats_available():
        pytest.skip(f"ai_hats not importable by dev-venv interpreter: {sys.executable}")
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(
        ["git", "init", "-q"],
        cwd=str(project),
        check=True,
        capture_output=True,
    )
    return project


def _run(cmd, *, cwd, expect_exit=0, timeout=60):
    env = os.environ.copy()
    # Avoid network / pip in the e2e: the binary on PATH is the one we
    # want under test.
    env["AI_HATS_NO_UPDATE"] = "1"
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
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


def test_init_writes_yaml_and_user_rules_dir_only(fresh_project):
    """``ai-hats self init -p claude -r assistant --no-wizard --no-update``
    produces ai-hats.yaml + an empty ``user-rules/``. NO root ./CLAUDE.md
    (HATS-1170), NO imports.md aggregator (HATS-1203), NO library/rules/* or
    library/skills/* materialization, NO .last_backup/.
    """
    project = fresh_project

    _run(
        [*AI_HATS, "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard", "--no-update"],
        cwd=project,
    )

    # ai-hats.yaml created with default_role + active_role split per HATS-407.
    yaml_path = project / PROJECT_CONFIG
    assert yaml_path.exists(), "ai-hats.yaml not created"
    body = yaml_path.read_text()
    assert "default_role: assistant" in body, body
    # active_role is the runtime cache — empty until first session_start.
    assert "active_role: ''" in body or 'active_role: ""' in body, body

    # HATS-1170 clean-root invariant: no scaffold in the project root.
    assert not (project / "CLAUDE.md").exists(), "root CLAUDE.md must not be created"

    # HATS-1203: the landing zone exists, the aggregator does not.
    canon = project / ".agent" / "ai-hats"
    assert (canon / "user-rules").is_dir(), "user-rules/ landing zone not created"
    assert not (canon / "imports.md").exists(), "imports.md aggregator was retired"

    # NO role-content materialization (HATS-407 contract).
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
        [*AI_HATS, "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard", "--no-update"],
        cwd=project,
    )

    canon = project / ".agent" / "ai-hats"
    # Snapshot canonical state immediately after init.
    initial_canonical = sorted(p.name for p in canon.iterdir())

    res = _run(
        [*AI_HATS, "config", "set", "-r", "sre"],
        cwd=project,
    )
    # CLI surfaces the new contract banner.
    assert "Default role" in res.stdout, res.stdout

    # default_role flipped; active_role stays empty (runtime cache).
    body = (project / PROJECT_CONFIG).read_text()
    assert "default_role: sre" in body, body
    assert "active_role: ''" in body or 'active_role: ""' in body, body

    # Canonical tree unchanged — config set must not regenerate anything.
    assert sorted(p.name for p in canon.iterdir()) == initial_canonical

    # No role-content materialization.
    for forbidden in ("priorities.md", "role.md", "skills_index.md"):
        assert not (canon / forbidden).exists()

    # No .last_backup/ — yaml-only path never creates one.
    assert not (canon / ".last_backup").exists()


# --------------------------------------------------------------------- #
# 3. bump — leaves user-rules alone, materializes nothing
# --------------------------------------------------------------------- #


def test_bump_leaves_user_rules_unmaterialized(fresh_project):
    """``ai-hats self bump`` after dropping a new user-rule must:
    - leave the rule file untouched and build no aggregator for it
      (HATS-1203: delivery is the composed prompt, not an on-disk index)
    - NOT materialize role-content files
    - NOT create .last_backup/
    """
    project = fresh_project
    _run(
        [*AI_HATS, "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard", "--no-update"],
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

    # The rule survives verbatim and no aggregator is built for it.
    assert (user_rules / "my-rule.md").read_text() == ("# my rule\n\nProject-specific guidance.\n")
    assert not (canon / "imports.md").exists(), "imports.md aggregator was retired"

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
        [*AI_HATS, "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard", "--no-update"],
        cwd=project,
    )

    res = _run(
        [*AI_HATS, "self", "rollback"],
        cwd=project,
        expect_exit=None,
    )
    assert res.returncode != 0, "self rollback should not exist post-HATS-407"
    blob = (res.stdout + res.stderr).lower()
    assert "no such command" in blob or "rollback" in blob, blob
