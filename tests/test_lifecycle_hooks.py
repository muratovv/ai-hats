"""HATS-1023 — consumer lifecycle-hook union materialization: union collector
over ALL library skills (not per-role), managed dir + manifest + sweep, LOUD
health-check (HYP-078/HATS-961), plan-sections config, and the two triggers
(HooksManager.materialize → self init / set_role; sync_hooks → session start).
"""

from pathlib import Path

import pytest
import yaml

from ai_hats.assembler import Assembler
from ai_hats.hooks_manager import HookSurface
from ai_hats.lifecycle_hooks import (
    LifecycleHookError,
    PLAN_SECTIONS_FILENAME,
    collect_plan_sections,
    expected_lifecycle_files,
    lifecycle_hooks_dir,
    materialize_lifecycle_hooks,
)
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


GOOD_SCRIPT = "#!/usr/bin/env bash\nexit 0\n"


def _skill(
    lib: Path,
    name: str,
    *,
    hooks: dict[str, list[str]] | None = None,
    sections: list[str] | None = None,
    script_body: str = GOOD_SCRIPT,
    write_scripts: bool = True,
) -> Path:
    """A library skill declaring lifecycle_hooks / plan_sections in frontmatter."""
    d = lib / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {name}", "ai_hats:"]
    if hooks:
        lines.append("  lifecycle_hooks:")
        for event, scripts in hooks.items():
            lines.append(f"    {event}:")
            lines.extend(f"      - {s}" for s in scripts)
    if sections:
        lines.append("  plan_sections:")
        lines.extend(f"    {s}" for s in sections)
    lines.append("---")
    (d / "SKILL.md").write_text("\n".join(lines) + f"\n# {name}\n")
    if write_scripts:
        for scripts in (hooks or {}).values():
            for rel in scripts:
                p = d / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(script_body)
    return d


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / ".agent").mkdir(parents=True)
    return p


@pytest.fixture
def lib(tmp_path: Path) -> Path:
    return tmp_path / "lib"


# ----- union collector -----


def test_union_collects_both_skills_not_last_wins(project, lib):
    _skill(lib, "aaa", hooks={"plan--execute": ["hooks/check.sh"]})
    _skill(lib, "bbb", hooks={"plan--execute": ["hooks/check.sh"]})
    materialize_lifecycle_hooks(project, [lib])
    event_d = lifecycle_hooks_dir(project) / "plan--execute.d"
    assert (event_d / "aaa-check.sh").is_file()
    assert (event_d / "bbb-check.sh").is_file()
    assert (event_d / "aaa-check.sh").stat().st_mode & 0o111
    manifest = (lifecycle_hooks_dir(project) / ".manifest").read_text()
    assert "plan--execute.d/aaa-check.sh" in manifest
    assert "plan--execute.d/bbb-check.sh" in manifest


def test_union_is_library_wide_not_role_scoped(project, lib):
    """The collector walks EVERY library skill — no composition involved."""
    _skill(lib, "unrelated-to-any-role", hooks={"document--review": ["gate.sh"]})
    materialize_lifecycle_hooks(project, [lib])
    assert (
        lifecycle_hooks_dir(project) / "document--review.d" / "unrelated-to-any-role-gate.sh"
    ).is_file()


def test_unknown_event_name_is_loud(project, lib):
    _skill(lib, "typo", hooks={"plann--execute": ["gate.sh"]})
    with pytest.raises(LifecycleHookError, match="typo.*unknown event 'plann--execute'"):
        materialize_lifecycle_hooks(project, [lib])


# ----- sweep -----


def test_sweep_removes_departed_skill_and_empty_event_dir(project, lib):
    keep = _skill(lib, "keeper", hooks={"plan--execute": ["k.sh"]})
    gone = _skill(lib, "goner", hooks={"document--review": ["g.sh"]})
    materialize_lifecycle_hooks(project, [lib])
    root = lifecycle_hooks_dir(project)
    assert (root / "document--review.d" / "goner-g.sh").is_file()

    import shutil

    shutil.rmtree(gone)
    materialize_lifecycle_hooks(project, [lib])
    assert not (root / "document--review.d").exists(), "empty event dir must be removed"
    assert (root / "plan--execute.d" / "keeper-k.sh").is_file()
    assert "goner" not in (root / ".manifest").read_text()
    assert keep.is_dir()


def test_no_dir_when_nothing_declared_now_or_before(project, lib):
    _skill(lib, "plain")
    materialize_lifecycle_hooks(project, [lib])
    assert not lifecycle_hooks_dir(project).exists()


# ----- health-check: loud at materialization, never a silent skip -----


def test_missing_declared_script_fails_loud(project, lib):
    _skill(lib, "broken", hooks={"plan--execute": ["nope.sh"]}, write_scripts=False)
    with pytest.raises(LifecycleHookError, match="broken.*nope.sh.*not found"):
        materialize_lifecycle_hooks(project, [lib])


def test_script_without_shebang_fails_loud(project, lib):
    _skill(lib, "noshebang", hooks={"plan--execute": ["h.sh"]}, script_body="exit 0\n")
    with pytest.raises(LifecycleHookError, match="noshebang.*no shebang"):
        materialize_lifecycle_hooks(project, [lib])


def test_empty_script_fails_loud(project, lib):
    _skill(lib, "hollow", hooks={"plan--execute": ["h.sh"]}, script_body="  \n")
    with pytest.raises(LifecycleHookError, match="hollow.*empty"):
        materialize_lifecycle_hooks(project, [lib])


# ----- plan-sections config -----


def test_plan_sections_union_dedupe_and_required_or(project, lib):
    _skill(lib, "aaa", sections=["- name: Rollback plan\n      required: false"])
    _skill(lib, "bbb", sections=["- Rollback plan", "- name: Risk log\n      required: false"])
    assert collect_plan_sections([lib]) == [
        {"name": "Rollback plan", "required": True},  # OR over declarations
        {"name": "Risk log", "required": False},
    ]
    materialize_lifecycle_hooks(project, [lib])
    cfg = lifecycle_hooks_dir(project) / PLAN_SECTIONS_FILENAME
    assert yaml.safe_load(cfg.read_text()) == collect_plan_sections([lib])


def test_plan_sections_config_swept_on_revert(project, lib):
    skill = _skill(lib, "aaa", sections=["- Rollback plan"])
    materialize_lifecycle_hooks(project, [lib])
    cfg = lifecycle_hooks_dir(project) / PLAN_SECTIONS_FILENAME
    assert cfg.is_file()

    import shutil

    shutil.rmtree(skill)
    materialize_lifecycle_hooks(project, [lib])
    assert not cfg.exists(), "reverted declaration must sweep the config"


# ----- triggers: HooksManager.materialize (self init) + sync drift surface -----


@pytest.fixture
def assembler(project, lib) -> Assembler:
    ProjectConfig(provider="agy").save(project / PROJECT_CONFIG)
    return Assembler(project_dir=project, library_paths=[lib])


def test_hooks_manager_materialize_runs_union_pass(assembler, project, lib):
    """The install-time trigger: `self init` / re-init / set_role end in
    HooksManager.materialize, which must include the lifecycle union."""
    _skill(lib, "gatekeeper", hooks={"plan--execute": ["g.sh"]})
    assembler.hooks.materialize(None)
    assert (lifecycle_hooks_dir(project) / "plan--execute.d" / "gatekeeper-g.sh").is_file()


def test_sync_surface_detects_and_heals_deleted_script(assembler, project, lib):
    """Session-start staleness self-heal: a swept/deleted managed hook is a
    LIFECYCLE drift and re-materialization restores it."""
    _skill(lib, "gatekeeper", hooks={"plan--execute": ["g.sh"]})
    assembler.hooks.materialize_lifecycle_hooks()
    dest = lifecycle_hooks_dir(project) / "plan--execute.d" / "gatekeeper-g.sh"
    dest.unlink()

    changes = assembler.hooks._lifecycle_hooks_changes()
    assert [(c.surface, c.name, c.kind) for c in changes] == [
        (HookSurface.LIFECYCLE, "plan--execute.d/gatekeeper-g.sh", "missing")
    ]
    assembler.hooks._heal_surfaces({HookSurface.LIFECYCLE}, None, None)
    assert dest.is_file()
    assert assembler.hooks._lifecycle_hooks_changes() == []


def test_sync_surface_detects_content_drift(assembler, project, lib):
    _skill(lib, "gatekeeper", hooks={"plan--execute": ["g.sh"]})
    assembler.hooks.materialize_lifecycle_hooks()
    dest = lifecycle_hooks_dir(project) / "plan--execute.d" / "gatekeeper-g.sh"
    dest.write_text("#!/usr/bin/env bash\nexit 1\n")
    kinds = {(c.name, c.kind) for c in assembler.hooks._lifecycle_hooks_changes()}
    assert ("plan--execute.d/gatekeeper-g.sh", "content") in kinds


def test_unwired_library_paths_skip_surface(project):
    """A HooksManager built without library_paths (bare test construction)
    skips the lifecycle surface instead of crashing."""
    from ai_hats.hooks_manager import HooksManager

    hm = HooksManager(
        project,
        ProjectConfig(provider="agy"),
        compose=lambda role: None,
        resolve_provider=lambda name: None,
    )
    hm.materialize_lifecycle_hooks()  # no-op, no raise
    assert hm._lifecycle_hooks_changes() == []


def test_expected_files_are_deterministic(lib):
    _skill(lib, "aaa", hooks={"plan--execute": ["a.sh"]}, sections=["- Rollback plan"])
    _skill(lib, "bbb", hooks={"document--review": ["b.sh"]})
    assert expected_lifecycle_files([lib]) == expected_lifecycle_files([lib])
