"""E2E: HATS-415 inline v0.6 → v0.7 migration via ``ai-hats self bump``.

Covers the four contracts (was HATS-408 P4 — relocated from
``self migrate-v07`` to inline ``bump``):

1. Refusal on user edit (default behaviour): exit 1, no writes.
2. ``--migrate-force`` bypass: sweep Tier 1+2, regenerate ``imports.md``,
   persist yaml hardening (``imports_order`` strip + ``default_role`` heal),
   stderr WARN per overwritten file. **No auto-commit** — the worktree
   carries unstaged deletions, user commits at leisure.
3. Idempotent rerun: second ``--migrate-force`` on the migrated tree is a
   no-op (nothing to sweep).
4. ``--check-branches``: surfaces a warning row when a sibling local
   branch touches a path the sweep would delete.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``.

Fixture strategy follows ``tests/e2e/test_self_update_heals_legacy_refs.py``:
one ``installed_launcher`` per *module* (cost: ~60s pip install + ~30s
self-update); every test pins to the shared venv via ``AI_HATS_VENV``.
Per-test cost stays under ~3s.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
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


def _git(project_dir: Path, *args: str, env: dict[str, str]) -> None:
    """Run a git command in ``project_dir``, raising on non-zero exit."""
    subprocess.run(
        ["git", *args], cwd=str(project_dir), env=env,
        check=True, capture_output=True, text=True,
    )


def _git_log_count(project_dir: Path, env: dict[str, str]) -> int:
    out = subprocess.run(
        ["git", "log", "--oneline"], cwd=str(project_dir), env=env,
        capture_output=True, text=True, check=True,
    )
    return len([line for line in out.stdout.splitlines() if line.strip()])


def _git_init_commit(project_dir: Path, env: dict[str, str]) -> None:
    _git(project_dir, "init", "-q", "-b", "main", env=env)
    _git(project_dir, "config", "user.email", "test@example.com", env=env)
    _git(project_dir, "config", "user.name", "Test", env=env)
    _git(project_dir, "add", "-A", env=env)
    _git(project_dir, "commit", "-q", "-m", "seed", env=env)


def _seed_v06_project(project_dir: Path) -> dict[str, Path]:
    """Materialise a synthetic v0.6 layout with knobs the gate cares about.

    Knobs:
      * ``imports_order`` deprecated field present in yaml.
      * ``active_role`` set, ``default_role`` absent (heal target).
      * ``priorities.md`` + ``role.md`` (USER-edited) + ``traits/foo.md``
        + ``rules/dev_rule_bar.md`` + ``skills_index.md`` materialised as
        the Tier-1 sweep targets.
      * ``library/rules/dev_rule_bar/rule.md`` as a Tier-2 mirror sample.
      * ``user-rules/keep_me.md`` to verify defence-in-depth: this MUST
        survive every --force sweep.

    Returns a name → absolute path map so tests can refer back to seeded
    artefacts without re-deriving paths.
    """
    (project_dir / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: assistant\n"
        "imports_order: role-first\n"
    )
    canonical = project_dir / ".agent" / "ai-hats"
    canonical.mkdir(parents=True)
    (canonical / "traits").mkdir()
    (canonical / "rules").mkdir()
    (canonical / "user-rules").mkdir()
    library_rules = canonical / "library" / "rules" / "dev_rule_bar"
    library_rules.mkdir(parents=True)
    # HATS-408 second-round review C1: v0.6 wrote hooks as FLAT scripts
    # under library/hooks/, not subdirectories. Seed the real v0.6 shape
    # so the sweep contract is locked end-to-end.
    library_hooks_parent = canonical / "library" / "hooks"
    library_hooks_parent.mkdir(parents=True)
    paths = {
        "priorities": canonical / "priorities.md",
        "role": canonical / "role.md",
        "trait_foo": canonical / "traits" / "foo.md",
        "rule_bar": canonical / "rules" / "dev_rule_bar.md",
        "skills_index": canonical / "skills_index.md",
        "library_rule_md": library_rules / "rule.md",
        "library_rule_meta": library_rules / "metadata.yaml",
        "library_hook_flat": library_hooks_parent / "session_start.sh",
        "user_rule": canonical / "user-rules" / "keep_me.md",
        "canonical_dir": canonical,
        "library_rule_dir": library_rules,
        "library_hooks_parent": library_hooks_parent,
    }
    paths["priorities"].write_text("# Priorities\n\n1. v0.6 placeholder\n")
    paths["role"].write_text(
        "# Role text from v0.6 materialised composition\n\n"
        "## USER-AUTHORED PARAGRAPH — must trigger refusal\n"
    )
    paths["trait_foo"].write_text("trait body\n")
    paths["rule_bar"].write_text("rule body\n")
    paths["skills_index"].write_text("# Skills Index\n\n- **alpha**\n")
    paths["library_rule_md"].write_text("# library mirror copy\n")
    paths["library_rule_meta"].write_text("kind: rule\n")
    paths["library_hook_flat"].write_text("#!/bin/sh\nexit 0\n")
    paths["user_rule"].write_text("# user rule — DO NOT TOUCH\n")
    # HATS-408 cross-task gate (round 3): real v0.6 projects always carry a
    # MANAGED manifest listing the framework files write_canonical wrote.
    # Seeding it locks the assembler.bump() refusal contract — without
    # this the gate is silently bypassed in tests.
    (canonical / "MANAGED").write_text(
        "# ai-hats canonical layer manifest. Do not edit.\n"
        "imports.md\n"
        "priorities.md\n"
        "role.md\n"
        "skills_index.md\n"
        "traits/foo.md\n"
        "rules/dev_rule_bar.md\n"
    )
    paths["managed"] = canonical / "MANAGED"
    return paths


@pytest.fixture(scope="module")
def installed_launcher(tmp_path_factory):
    """Install ai-hats once per module (~60s pip + ~30s self-update).

    Every test in this module pins to the same venv via ``AI_HATS_VENV`` so
    the per-test cost stays in the ~1–3s range.
    """
    tmp = tmp_path_factory.mktemp("launcher")
    launcher_dest = tmp / "bin" / "ai-hats"
    launcher_dest.parent.mkdir(parents=True)

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(REPO_ROOT)
    env.pop("AI_HATS_VENV", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp, env=env, timeout=30)
    bootstrap_proj = tmp / "_bootstrap_proj"
    bootstrap_proj.mkdir()
    _run(
        [str(launcher_dest), "self", "update"],
        cwd=bootstrap_proj, env=env, timeout=180,
    )
    shared_venv = bootstrap_proj / ".agent" / "ai-hats" / ".venv"
    assert shared_venv.is_dir(), "bootstrap did not create shared venv"
    env["AI_HATS_VENV"] = str(shared_venv)
    return launcher_dest, env


# ----- Test 1: default behaviour refuses, makes no changes -----


@pytest.mark.integration
def test_e2e_refuse_on_user_edit_default_behavior(installed_launcher, tmp_path):
    launcher, env = installed_launcher
    project = tmp_path / "proj"
    project.mkdir()
    paths = _seed_v06_project(project)
    _git_init_commit(project, env)

    original_role = paths["role"].read_bytes()
    original_priorities = paths["priorities"].read_bytes()
    original_yaml = (project / "ai-hats.yaml").read_bytes()
    before_commits = _git_log_count(project, env)

    res = _run(
        [str(launcher), "self", "init"],
        cwd=project, env=env, timeout=60, expect_exit=1,
    )

    combined = res.stdout + res.stderr
    assert "v0.6 canonical layout detected" in combined, combined
    assert "user edits found on disk" in combined, combined
    assert "role.md" in combined, combined
    assert "user-rules" in combined, combined  # guidance pointer
    assert "--migrate-force" in combined, combined  # next-step hint

    # No writes anywhere — bump refused before any sweep / yaml normalize.
    assert paths["role"].read_bytes() == original_role
    assert paths["priorities"].read_bytes() == original_priorities
    assert (project / "ai-hats.yaml").read_bytes() == original_yaml
    assert paths["library_rule_md"].is_file()
    assert paths["user_rule"].read_text() == "# user rule — DO NOT TOUCH\n"

    # No commit (HATS-415: bump never commits) and no staging.
    assert _git_log_count(project, env) == before_commits
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(project), env=env, check=False,
    )
    assert diff.returncode == 0, "expected empty staging area after refusal"

    # The yaml-load WARNs (imports_order strip + default_role heal) fire on
    # the refuse path too — that's the contract: WARNs are about yaml shape,
    # not about migration.
    assert "imports_order" in res.stderr
    assert "default_role" in res.stderr


# ----- Test 2: --force bypass, atomic single commit -----


@pytest.mark.integration
def test_e2e_migrate_force_bypass_sweeps_without_commit(installed_launcher, tmp_path):
    """HATS-415: ``self bump --migrate-force`` overwrites user-edited v0.6
    files and continues with the rest of bump (yaml normalize, scaffold,
    write_canonical). No atomic commit — the worktree carries unstaged
    deletions. Variant B (auto-commit) was explicitly rejected during the
    HATS-415 design fork."""
    launcher, env = installed_launcher
    project = tmp_path / "proj"
    project.mkdir()
    paths = _seed_v06_project(project)
    _git_init_commit(project, env)
    before_commits = _git_log_count(project, env)

    res = _run(
        [f"{env["AI_HATS_VENV"]}/bin/python", "-m", "ai_hats._bump_internal", "--migrate-force"],
        cwd=project, env=env, timeout=60, expect_exit=0,
    )

    # Tier 1 wiped.
    assert not paths["priorities"].exists()
    assert not paths["role"].exists()
    assert not paths["skills_index"].exists()
    assert not (paths["canonical_dir"] / "traits").exists()
    assert not (paths["canonical_dir"] / "rules").exists()
    # Tier 2 wiped — rules subdir (whole tree) AND hooks flat file.
    assert not paths["library_rule_dir"].exists()
    assert not paths["library_hook_flat"].exists()
    # imports.md regenerated (v0.7 shape — sorted user-rules aggregator).
    imports_md = paths["canonical_dir"] / "imports.md"
    assert imports_md.is_file()
    assert imports_md.read_text() == "@./user-rules/keep_me.md\n", \
        f"unexpected imports.md content: {imports_md.read_text()!r}"
    # MANAGED rewritten to list only imports.md (modulo a comment header).
    managed = paths["canonical_dir"] / "MANAGED"
    assert managed.is_file()
    managed_entries = [
        line.strip()
        for line in managed.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert managed_entries == ["imports.md"], managed_entries
    # user-rules untouched (defence in depth).
    assert paths["user_rule"].read_text() == "# user rule — DO NOT TOUCH\n"

    # Yaml hardened on disk by ``_normalize_yaml`` (runs after migration).
    import yaml as _yaml
    saved = _yaml.safe_load((project / "ai-hats.yaml").read_text())
    assert "imports_order" not in saved, saved
    assert saved.get("default_role") == "assistant", saved

    # NO auto-commit — bump does not commit. The user reviews and commits
    # via ``git status`` / ``git commit`` at leisure.
    assert _git_log_count(project, env) == before_commits, \
        "bump must not create commits on the user's behalf"

    # stderr WARN per overwritten user-edit file.
    assert "overwriting" in res.stderr
    assert "role.md" in res.stderr


# ----- Test 3: idempotent rerun -----


@pytest.mark.integration
def test_e2e_idempotent_rerun(installed_launcher, tmp_path):
    """Second ``self bump --migrate-force`` on a migrated tree is a no-op:
    no Tier-1 findings → migration skips entirely (HATS-415 trigger
    contract). Subsequent bump work (scaffold, write_canonical) is also
    idempotent on a clean v0.7 layout."""
    launcher, env = installed_launcher
    project = tmp_path / "proj"
    project.mkdir()
    _seed_v06_project(project)
    _git_init_commit(project, env)

    _run(
        [f"{env["AI_HATS_VENV"]}/bin/python", "-m", "ai_hats._bump_internal", "--migrate-force"],
        cwd=project, env=env, timeout=60, expect_exit=0,
    )
    # Snapshot the canonical dir after first run.
    canonical = project / ".agent" / "ai-hats"
    after_first_bytes = {
        p.relative_to(canonical).as_posix(): p.read_bytes()
        for p in canonical.rglob("*")
        if p.is_file()
    }

    res = _run(
        [f"{env["AI_HATS_VENV"]}/bin/python", "-m", "ai_hats._bump_internal", "--migrate-force"],
        cwd=project, env=env, timeout=60, expect_exit=0,
    )

    # Second pass: no Tier-1 left to sweep, no WARN re-emission.
    assert "overwriting" not in res.stderr, res.stderr
    # Canonical tree bytes are stable (idempotent).
    after_second_bytes = {
        p.relative_to(canonical).as_posix(): p.read_bytes()
        for p in canonical.rglob("*")
        if p.is_file()
    }
    assert after_first_bytes == after_second_bytes, \
        "second bump --migrate-force changed canonical bytes"


# ----- Test 4: --check-branches surfaces a sibling-branch warning -----


# ----- Test 4: --check-branches surfaces a sibling-branch warning -----


@pytest.mark.integration
def test_e2e_check_branches_warns(installed_launcher, tmp_path):
    """``--check-branches`` is additive: surfaces a stderr WARN row when a
    sibling branch modifies a path slated for deletion. Does not block —
    the user-edit refusal still fires for the same reason it would
    without the flag."""
    launcher, env = installed_launcher
    project = tmp_path / "proj"
    project.mkdir()
    paths = _seed_v06_project(project)
    _git_init_commit(project, env)

    # Sibling branch with an edit to a soon-to-be-deleted path.
    _git(project, "checkout", "-q", "-b", "sibling", env=env)
    paths["priorities"].write_text("# Priorities\n\n1. sibling edit\n")
    _git(project, "commit", "-aq", "-m", "wip on sibling", env=env)
    _git(project, "checkout", "-q", "main", env=env)

    res = _run(
        [f"{env["AI_HATS_VENV"]}/bin/python", "-m", "ai_hats._bump_internal", "--check-branches"],
        cwd=project, env=env, timeout=60, expect_exit=1,
    )

    # The refusal still fires (seed has a USER-AUTHORED paragraph in role.md);
    # the branch warning is additive on stderr.
    combined = res.stdout + res.stderr
    assert "sibling" in combined, combined
    assert ".agent/ai-hats/priorities.md" in combined, combined
    assert "v0.7-migration paths" in combined, combined
