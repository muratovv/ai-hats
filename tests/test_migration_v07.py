"""Unit tests for HATS-408 P2: migration_v07 core module.

Coverage:
  * collect_tier1 / collect_tier2 (manifest-blind globbing).
  * Baseline renderers (priorities, role, trait, rule, skills_index).
  * is_user_edit whitespace normalisation (no false positives on editor churn).
  * plan_migration end-to-end (compose result + on-disk fixture → report).
  * detect_yaml_changes peeks at raw yaml dict.
  * execute_deletions sweeps + protects user-rules.
  * check_branches_modify_paths against a real git fixture.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats.composer import CompositionResult, ResolvedComponent
from ai_hats.models import ComponentType, HooksConfig, ProjectConfig
from ai_hats import migration_v07 as m


# ---------- Test helpers ----------


def _seed_tier1(canonical: Path) -> dict[str, Path]:
    """Materialise a v0.6 Tier-1 fixture. Returns name → path map."""
    canonical.mkdir(parents=True, exist_ok=True)
    (canonical / "traits").mkdir()
    (canonical / "rules").mkdir()
    files = {
        "priorities": canonical / "priorities.md",
        "role": canonical / "role.md",
        "skill_index": canonical / "skills_index.md",
        "trait_foo": canonical / "traits" / "foo.md",
        "rule_bar": canonical / "rules" / "bar.md",
    }
    for p in files.values():
        p.write_text("placeholder\n")
    return files


def _seed_tier2(canonical: Path) -> dict[str, Path]:
    """Materialise a v0.6 Tier-2 fixture. Returns name → mirror-dir map."""
    rules_parent = canonical / "library" / "rules"
    skills_parent = canonical / "library" / "skills"
    rules_parent.mkdir(parents=True, exist_ok=True)
    skills_parent.mkdir(parents=True, exist_ok=True)
    rule_dir = rules_parent / "dev_rule_bar"
    skill_dir = skills_parent / "backlog-manager"
    rule_dir.mkdir()
    skill_dir.mkdir()
    (rule_dir / "rule.md").write_text("# rule body\n")
    (rule_dir / "metadata.yaml").write_text("kind: rule\n")
    (skill_dir / "SKILL.md").write_text("---\ndescription: backlog\n---\nbody\n")
    return {"dev_rule_bar": rule_dir, "backlog-manager": skill_dir}


def _make_compose(
    *,
    priorities: list[str] | None = None,
    role_injection: str = "",
    overlay_injection: str = "",
    trait_injections: dict[str, str] | None = None,
    rules: list[ResolvedComponent] | None = None,
    skills: list[ResolvedComponent] | None = None,
) -> CompositionResult:
    return CompositionResult(
        name="dev",
        priorities=priorities or [],
        rules=rules or [],
        skills=skills or [],
        hooks=HooksConfig(),
        injections=[],
        trait_injections=trait_injections or {},
        role_injection=role_injection,
        overlay_injection=overlay_injection,
    )


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=False, timeout=10,
    )


# ---------- Collectors ----------


def test_collect_tier1_finds_all_known_shapes(tmp_path):
    canonical = tmp_path / ".agent" / "ai-hats"
    files = _seed_tier1(canonical)

    found = m.collect_tier1(canonical)

    paths = {p for p, _ in found}
    kinds = {p.stem: k for p, k in found}
    assert paths == set(files.values())
    assert kinds["priorities"] == "priorities"
    assert kinds["role"] == "role"
    assert kinds["skills_index"] == "skill_index"
    assert kinds["foo"] == "trait"
    assert kinds["bar"] == "rule"


def test_collect_tier1_ignores_unrelated_files(tmp_path):
    canonical = tmp_path / "ai-hats"
    canonical.mkdir()
    (canonical / "imports.md").write_text("@./user-rules/x.md\n")
    (canonical / "MANAGED").write_text("imports.md\n")
    (canonical / "user-rules").mkdir()
    (canonical / "user-rules" / "x.md").write_text("user content\n")

    found = m.collect_tier1(canonical)

    # imports.md / MANAGED / user-rules/ are not v0.6 sweep targets.
    assert found == []


def test_collect_tier1_missing_canonical_dir_returns_empty(tmp_path):
    assert m.collect_tier1(tmp_path / "nope") == []


def test_collect_tier2_finds_mirror_dirs(tmp_path):
    canonical = tmp_path / "ai-hats"
    mirrors = _seed_tier2(canonical)

    found = m.collect_tier2(canonical)

    paths = {p for p, _ in found}
    kinds = {p.name: k for p, k in found}
    assert paths == set(mirrors.values())
    assert kinds["dev_rule_bar"] == "lib_rule_dir"
    assert kinds["backlog-manager"] == "lib_skill_dir"


def test_collect_tier2_finds_hooks_mirror(tmp_path):
    """HATS-408 review B1: v0.6 also materialised library/hooks/<name>/.

    Without this finder, a v0.6 project shipping a hook keeps a stale
    ``library/hooks/<name>/`` tree after ``--force`` — breaks the
    release-gate contract that the sweep is total.
    """
    canonical = tmp_path / "ai-hats"
    hooks_parent = canonical / "library" / "hooks"
    hooks_parent.mkdir(parents=True)
    hook_dir = hooks_parent / "pre-commit-attachments"
    hook_dir.mkdir()
    (hook_dir / "pre-commit").write_text("#!/bin/sh\nexit 0\n")

    found = m.collect_tier2(canonical)

    by_name = {p.name: k for p, k in found}
    assert by_name == {"pre-commit-attachments": "lib_hook_dir"}


def test_collect_tier2_ignores_dotfiles_at_parent_level(tmp_path):
    canonical = tmp_path / "ai-hats"
    parent = canonical / "library" / "rules"
    parent.mkdir(parents=True)
    (parent / ".library_rules").write_text("dev_rule_bar\n")
    (parent / ".hidden").mkdir()

    found = m.collect_tier2(canonical)

    assert found == []


# ---------- Baseline renderers ----------


def test_render_priorities_md_matches_v06_format():
    out = m.render_priorities_md(["Reliability", "Cleanliness", "Velocity"])
    assert out == "# Priorities\n\n1. Reliability\n2. Cleanliness\n3. Velocity\n"


def test_render_priorities_md_empty_returns_none():
    assert m.render_priorities_md([]) is None


def test_render_role_md_joins_with_blank_line():
    out = m.render_role_md("role text", "overlay text")
    assert out == "role text\n\noverlay text\n"


def test_render_role_md_role_only():
    assert m.render_role_md("role text", "") == "role text\n"


def test_render_role_md_both_empty_returns_none():
    assert m.render_role_md("", "") is None


def test_render_trait_and_rule_add_trailing_newline():
    assert m.render_trait_md("body") == "body\n"
    assert m.render_trait_md("body\n") == "body\n"
    assert m.render_rule_md("") is None


def test_render_skills_index_md_top_section(tmp_path):
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text('---\ndescription: "alpha skill"\n---\nbody\n')
    rc = ResolvedComponent(
        name="alpha",
        component_type=ComponentType.SKILL,
        source_path=skill_dir,
        injection="",
    )
    out = m.render_skills_index_md([rc])
    assert out == "# Skills Index\n\n- **alpha** — alpha skill\n"


def test_render_skills_index_md_empty():
    assert m.render_skills_index_md([]) is None


def test_render_skills_index_md_skips_description_when_equal_to_name(tmp_path):
    skill_dir = tmp_path / "beta"
    skill_dir.mkdir()
    # No SKILL.md at all → _skill_description returns "" → bullet without dash-em.
    rc = ResolvedComponent(
        name="beta",
        component_type=ComponentType.SKILL,
        source_path=skill_dir,
        injection="",
    )
    out = m.render_skills_index_md([rc])
    assert out == "# Skills Index\n\n- **beta**\n"


# ---------- Diff engine ----------


def test_is_user_edit_false_on_identical():
    assert m.is_user_edit(b"hello\n", "hello\n") is False


def test_is_user_edit_false_on_trailing_whitespace_only_diff():
    assert m.is_user_edit(b"hello   \n", "hello\n") is False


def test_is_user_edit_false_on_blank_line_collapse():
    assert m.is_user_edit(b"a\n\n\n\nb\n", "a\n\nb\n") is False


def test_is_user_edit_false_on_missing_trailing_newline():
    assert m.is_user_edit(b"hello", "hello\n") is False


def test_is_user_edit_true_on_content_change():
    assert m.is_user_edit(b"hello world\n", "hello\n") is True


def test_is_user_edit_true_when_baseline_none():
    assert m.is_user_edit(b"anything\n", None) is True


def test_is_user_edit_true_on_binary_garbage():
    # Decode fails → treated as user edit (cannot prove safe).
    assert m.is_user_edit(b"\xff\xfe\x00\x01", "hello\n") is True


# ---------- Tier 2 baseline ----------


def test_render_tier2_dir_baseline_matches_source(tmp_path):
    mirror = tmp_path / "mirror" / "rule_x"
    mirror.mkdir(parents=True)
    (mirror / "rule.md").write_text("body\n")
    source_root = tmp_path / "source" / "rule_x"
    source_root.mkdir(parents=True)
    (source_root / "rule.md").write_text("body\n")
    baseline = m.render_tier2_dir_baseline(mirror, {"rule_x": source_root})
    actual = m.render_tier2_dir_actual(mirror)
    assert baseline == actual


def test_render_tier2_dir_baseline_none_when_source_missing(tmp_path):
    mirror = tmp_path / "mirror" / "rule_x"
    mirror.mkdir(parents=True)
    (mirror / "rule.md").write_text("body\n")
    assert m.render_tier2_dir_baseline(mirror, {}) is None
    assert m.render_tier2_dir_baseline(mirror, None) is None


def test_render_tier2_dir_baseline_ignores_bookkeeping_files(tmp_path):
    mirror = tmp_path / "mirror" / "rule_x"
    mirror.mkdir(parents=True)
    (mirror / "rule.md").write_text("body\n")
    (mirror / ".library_rules").write_text("rule_x\n")  # bookkeeping, ignored
    source_root = tmp_path / "source" / "rule_x"
    source_root.mkdir(parents=True)
    (source_root / "rule.md").write_text("body\n")
    baseline = m.render_tier2_dir_baseline(mirror, {"rule_x": source_root})
    assert baseline is not None
    assert ".library_rules" not in baseline


# ---------- Planner integration ----------


def test_plan_migration_no_user_edits_when_disk_matches_baseline(tmp_path):
    canonical = tmp_path / "ai-hats"
    files = _seed_tier1(canonical)
    # Write the actual baseline content so no diff.
    files["priorities"].write_text(m.render_priorities_md(["Reliability", "Cleanliness"]))
    files["role"].write_text(m.render_role_md("role injection text", ""))
    files["skill_index"].unlink()  # skip skill_index for this case
    files["trait_foo"].write_text(m.render_trait_md("trait body"))
    files["rule_bar"].write_text(m.render_rule_md("rule body"))

    compose = _make_compose(
        priorities=["Reliability", "Cleanliness"],
        role_injection="role injection text",
        trait_injections={"foo": "trait body"},
        rules=[ResolvedComponent(name="bar", component_type=ComponentType.RULE,
                                 source_path=tmp_path, injection="rule body")],
    )

    report = m.plan_migration(canonical, compose)

    assert report.user_edits == []
    assert {f.path.name for f in report.safe_deletions} == {
        "priorities.md", "role.md", "foo.md", "bar.md"
    }
    assert all(f.baseline_present for f in report.findings)


def test_plan_migration_flags_user_edits_beyond_whitespace(tmp_path):
    canonical = tmp_path / "ai-hats"
    files = _seed_tier1(canonical)
    files["priorities"].write_text(m.render_priorities_md(["Reliability"]))
    files["role"].write_text(m.render_role_md("base role", "") + "\n\nUSER ADDED PARAGRAPH\n")
    files["skill_index"].unlink()
    files["trait_foo"].write_text(m.render_trait_md("trait body"))
    files["rule_bar"].write_text(m.render_rule_md("rule body"))

    compose = _make_compose(
        priorities=["Reliability"],
        role_injection="base role",
        trait_injections={"foo": "trait body"},
        rules=[ResolvedComponent(name="bar", component_type=ComponentType.RULE,
                                 source_path=tmp_path, injection="rule body")],
    )

    report = m.plan_migration(canonical, compose)

    assert [f.path.name for f in report.user_edits] == ["role.md"]


def test_plan_migration_baseline_missing_for_unknown_trait_classifies_as_edit(tmp_path):
    canonical = tmp_path / "ai-hats"
    (canonical / "traits").mkdir(parents=True)
    orphan = canonical / "traits" / "orphan.md"
    orphan.write_text("legacy trait body\n")
    compose = _make_compose()  # composition has no traits

    report = m.plan_migration(canonical, compose)

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.is_user_edit is True
    assert finding.baseline_present is False
    assert finding.path == orphan


def test_plan_migration_tier2_baselineless_classifies_as_edit(tmp_path):
    canonical = tmp_path / "ai-hats"
    _seed_tier2(canonical)
    compose = _make_compose()

    report = m.plan_migration(canonical, compose, tier2_source_lookup=None)

    tier2 = [f for f in report.findings if f.tier == 2]
    assert len(tier2) == 2
    assert all(f.is_user_edit and not f.baseline_present for f in tier2)


def test_plan_migration_tier2_safe_when_mirror_matches_source(tmp_path):
    canonical = tmp_path / "ai-hats"
    mirror_root = canonical / "library" / "rules"
    mirror_root.mkdir(parents=True)
    (mirror_root / "rule_x").mkdir()
    (mirror_root / "rule_x" / "rule.md").write_text("body\n")
    source = tmp_path / "src" / "rule_x"
    source.mkdir(parents=True)
    (source / "rule.md").write_text("body\n")
    compose = _make_compose()

    report = m.plan_migration(canonical, compose, tier2_source_lookup={"rule_x": source})

    tier2 = [f for f in report.findings if f.tier == 2]
    assert len(tier2) == 1
    assert tier2[0].is_user_edit is False
    assert tier2[0].baseline_present is True


# ---------- yaml change detection ----------


def test_detect_yaml_changes_flags_imports_order_and_heal(tmp_path):
    raw = {
        "schema_version": 4,
        "provider": "claude",
        "ai_hats_dir": ".agent/ai-hats",
        "active_role": "dev",
        "imports_order": "role-first",
    }
    cfg = ProjectConfig(provider="claude", ai_hats_dir=".agent/ai-hats",
                        active_role="dev", default_role="dev")
    changes = m.detect_yaml_changes(raw, cfg)
    assert any("imports_order" in c for c in changes)
    assert any("heal default_role" in c for c in changes)


def test_detect_yaml_changes_silent_on_clean_yaml():
    raw = {"provider": "claude", "active_role": "dev", "default_role": "dev"}
    cfg = ProjectConfig(provider="claude", active_role="dev", default_role="dev")
    assert m.detect_yaml_changes(raw, cfg) == []


# ---------- execute_deletions ----------


def test_execute_deletions_removes_findings_and_empty_parents(tmp_path):
    canonical = tmp_path / "ai-hats"
    files = _seed_tier1(canonical)
    report = m.MigrationReport(findings=[
        m.TierFinding(path=files["trait_foo"], tier=1, kind="trait",
                      is_user_edit=False, baseline_present=True),
        m.TierFinding(path=files["rule_bar"], tier=1, kind="rule",
                      is_user_edit=False, baseline_present=True),
    ])

    removed = m.execute_deletions(report, canonical)

    assert files["trait_foo"].resolve() in removed
    assert files["rule_bar"].resolve() in removed
    # Empty parents swept.
    assert not (canonical / "traits").exists()
    assert not (canonical / "rules").exists()
    # Sibling Tier-1 files untouched.
    assert files["priorities"].exists()


def test_execute_deletions_never_touches_user_rules(tmp_path):
    canonical = tmp_path / "ai-hats"
    user_rules = canonical / "user-rules"
    user_rules.mkdir(parents=True)
    sacred = user_rules / "do-not-touch.md"
    sacred.write_text("user content\n")
    # Construct a malicious report attempting to delete a user-rules file.
    report = m.MigrationReport(findings=[
        m.TierFinding(path=sacred, tier=1, kind="trait",
                      is_user_edit=False, baseline_present=True),
    ])

    m.execute_deletions(report, canonical)

    assert sacred.exists()


def test_execute_deletions_unlinks_symlink_without_following_target(tmp_path):
    """HATS-408 review B2: a malicious symlink finding must be unlinked,
    not followed — the link target (e.g. /etc/passwd) must survive."""
    canonical = tmp_path / "ai-hats"
    (canonical / "traits").mkdir(parents=True)
    outside = tmp_path / "outside_target.txt"
    outside.write_text("DO NOT DELETE ME\n")
    link = canonical / "traits" / "foo.md"
    link.symlink_to(outside)
    report = m.MigrationReport(findings=[
        m.TierFinding(path=link, tier=1, kind="trait",
                      is_user_edit=False, baseline_present=True),
    ])

    m.execute_deletions(report, canonical)

    # Symlink itself gone.
    assert not link.exists() and not link.is_symlink()
    # Outside target survived.
    assert outside.read_text() == "DO NOT DELETE ME\n"


def test_execute_deletions_does_not_rmtree_through_symlinked_dir(tmp_path):
    """A symlinked Tier-2 mirror dir must be unlinked, not rmtree'd into."""
    canonical = tmp_path / "ai-hats"
    mirror_parent = canonical / "library" / "rules"
    mirror_parent.mkdir(parents=True)
    outside_dir = tmp_path / "external_lib"
    outside_dir.mkdir()
    (outside_dir / "important.md").write_text("KEEP\n")
    link = mirror_parent / "fake_rule"
    link.symlink_to(outside_dir)
    report = m.MigrationReport(findings=[
        m.TierFinding(path=link, tier=2, kind="lib_rule_dir",
                      is_user_edit=False, baseline_present=True),
    ])

    m.execute_deletions(report, canonical)

    assert not link.exists() and not link.is_symlink()
    # External dir + its contents untouched.
    assert outside_dir.is_dir()
    assert (outside_dir / "important.md").read_text() == "KEEP\n"


def test_execute_deletions_idempotent_on_missing_paths(tmp_path):
    canonical = tmp_path / "ai-hats"
    canonical.mkdir()
    ghost = canonical / "rules" / "missing.md"
    report = m.MigrationReport(findings=[
        m.TierFinding(path=ghost, tier=1, kind="rule",
                      is_user_edit=False, baseline_present=True),
    ])
    removed = m.execute_deletions(report, canonical)
    assert removed == []


# ---------- check_branches_modify_paths ----------


@pytest.fixture
def git_project(tmp_path):
    """A real git project with two branches differing in canonical content."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    canonical = tmp_path / ".agent" / "ai-hats"
    canonical.mkdir(parents=True)
    (canonical / "priorities.md").write_text("v1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    # Sibling branch with an edit.
    _git(tmp_path, "checkout", "-q", "-b", "sibling")
    (canonical / "priorities.md").write_text("v2 from sibling\n")
    _git(tmp_path, "commit", "-aq", "-m", "wip on sibling")
    _git(tmp_path, "checkout", "-q", "main")
    return tmp_path


def test_check_branches_modify_paths_finds_sibling(git_project):
    canonical = git_project / ".agent" / "ai-hats"
    findings = m.check_branches_modify_paths(
        git_project, [canonical / "priorities.md"]
    )
    branches = {b for b, _ in findings}
    assert "sibling" in branches
    sibling_paths = {p for b, paths in findings if b == "sibling" for p in paths}
    assert ".agent/ai-hats/priorities.md" in sibling_paths


def test_check_branches_modify_paths_returns_empty_on_no_git(tmp_path):
    # No `git init` → for-each-ref fails → graceful empty.
    assert m.check_branches_modify_paths(tmp_path, [tmp_path / "x"]) == []


def test_check_branches_modify_paths_empty_input_returns_empty(git_project):
    assert m.check_branches_modify_paths(git_project, []) == []
