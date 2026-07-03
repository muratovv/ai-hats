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

import logging
import subprocess
from pathlib import Path

import pytest

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.models import ProjectConfig
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


def test_collect_tier2_finds_hooks_as_flat_files(tmp_path):
    """HATS-408 second-round review C1: v0.6 wrote hooks as FLAT scripts
    (``library/hooks/session_start.sh``), not subdirectories. The original
    B1 fix used dir-mode which missed the v0.6 shape entirely AND would
    have swept undocumented user-owned ``library/hooks/<subdir>/`` content.
    File-mode finder catches the real v0.6 leftover and leaves user subdirs
    alone (any user-authored subdir lives outside the v0.6/v0.7 contract).
    """
    canonical = tmp_path / "ai-hats"
    hooks_parent = canonical / "library" / "hooks"
    hooks_parent.mkdir(parents=True)
    (hooks_parent / "session_start.sh").write_text("#!/bin/sh\necho hi\n")
    (hooks_parent / "session_end.py").write_text("print('bye')\n")
    # User-authored subdir — out of v0.6 contract; must NOT be a finding.
    user_subdir = hooks_parent / "user_owned"
    user_subdir.mkdir()
    (user_subdir / "custom.sh").write_text("user script\n")
    # Bookkeeping marker — must NOT be a finding (dotfile).
    (hooks_parent / ".ai-hats-managed").write_text("session_start.sh\nsession_end.py\n")
    # Unrelated suffix — must NOT be a finding.
    (hooks_parent / "README.md").write_text("docs\n")

    found = m.collect_tier2(canonical)

    by_name = {p.name: k for p, k in found}
    assert by_name == {
        "session_start.sh": "lib_hook_file",
        "session_end.py": "lib_hook_file",
    }
    # Sanity: user-owned subdir is not a finding.
    assert "user_owned" not in by_name


def test_render_tier2_hook_file_baseline_matches_source(tmp_path):
    """Hook source file in library_paths' hooks root → byte-equal baseline."""
    mirror = tmp_path / "ai-hats" / "library" / "hooks"
    mirror.mkdir(parents=True)
    hook = mirror / "session_start.sh"
    hook.write_text("#!/bin/sh\nexit 0\n")
    source_root = tmp_path / "lib" / "hooks"
    source_root.mkdir(parents=True)
    (source_root / "session_start.sh").write_text("#!/bin/sh\nexit 0\n")
    baseline = m.render_tier2_hook_file_baseline(hook, [source_root])
    assert baseline == hook.read_text()


def test_render_tier2_hook_file_baseline_none_when_source_missing(tmp_path):
    """No matching hook in any source dir → None → caller classifies as user-edit."""
    mirror = tmp_path / "ai-hats" / "library" / "hooks"
    mirror.mkdir(parents=True)
    hook = mirror / "session_start.sh"
    hook.write_text("#!/bin/sh\n")
    other_source = tmp_path / "lib" / "hooks"
    other_source.mkdir(parents=True)
    (other_source / "session_end.sh").write_text("#!/bin/sh\n")  # wrong name
    assert m.render_tier2_hook_file_baseline(hook, [other_source]) is None
    assert m.render_tier2_hook_file_baseline(hook, []) is None
    assert m.render_tier2_hook_file_baseline(hook, None) is None


def test_plan_migration_tier2_hook_file_safe_when_source_matches(tmp_path):
    canonical = tmp_path / "ai-hats"
    hooks_parent = canonical / "library" / "hooks"
    hooks_parent.mkdir(parents=True)
    (hooks_parent / "session_start.sh").write_text("#!/bin/sh\nbody\n")
    source_root = tmp_path / "lib" / "hooks"
    source_root.mkdir(parents=True)
    (source_root / "session_start.sh").write_text("#!/bin/sh\nbody\n")
    compose = _make_compose()

    report = m.plan_migration(
        canonical, compose, tier2_hook_source_dirs=[source_root]
    )

    findings = [f for f in report.findings if f.kind == "lib_hook_file"]
    assert len(findings) == 1
    assert findings[0].is_user_edit is False
    assert findings[0].baseline_present is True


def test_plan_migration_tier2_hook_file_user_edit_when_source_diverges(tmp_path):
    canonical = tmp_path / "ai-hats"
    hooks_parent = canonical / "library" / "hooks"
    hooks_parent.mkdir(parents=True)
    (hooks_parent / "session_start.sh").write_text("#!/bin/sh\nMODIFIED BY USER\n")
    source_root = tmp_path / "lib" / "hooks"
    source_root.mkdir(parents=True)
    (source_root / "session_start.sh").write_text("#!/bin/sh\noriginal\n")
    compose = _make_compose()

    report = m.plan_migration(
        canonical, compose, tier2_hook_source_dirs=[source_root]
    )

    findings = [f for f in report.findings if f.kind == "lib_hook_file"]
    assert len(findings) == 1
    assert findings[0].is_user_edit is True
    assert findings[0].baseline_present is True


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
        component_type=ComponentKind.SKILL,
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
        component_type=ComponentKind.SKILL,
        source_path=skill_dir,
        injection="",
    )
    out = m.render_skills_index_md([rc])
    assert out == "# Skills Index\n\n- **beta**\n"


def test_skill_description_malformed_warns_then_falls_back(tmp_path, caplog):
    """HATS-813: a malformed frontmatter block must not abort the migration diff;
    the description lookup falls back to "" — but logs a WARNING so the broken
    skill is distinguishable from one that simply declares no description."""
    skill_dir = tmp_path / "skills" / "gamma"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nbad: : indent\n---\nbody\n")
    rc = ResolvedComponent(
        name="gamma",
        component_type=ComponentKind.SKILL,
        source_path=skill_dir,
        injection="",
    )
    with caplog.at_level(logging.WARNING, logger="ai_hats.migration_v07"):
        assert m._skill_description(rc) == ""
    assert "malformed" in caplog.text and "gamma" in caplog.text
    assert m.render_skills_index_md([rc]) == "# Skills Index\n\n- **gamma**\n"


def test_skill_description_absent_is_silent(tmp_path, caplog):
    """Contrast: no SKILL.md → "" with NO warning (only malformed is noisy)."""
    skill_dir = tmp_path / "delta"
    skill_dir.mkdir()
    rc = ResolvedComponent(
        name="delta",
        component_type=ComponentKind.SKILL,
        source_path=skill_dir,
        injection="",
    )
    with caplog.at_level(logging.WARNING, logger="ai_hats.migration_v07"):
        assert m._skill_description(rc) == ""
    assert caplog.text == ""


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
    # HATS-700: the rule body is read on demand from source_path/rule.md
    # (the composer no longer eager-loads it into ResolvedComponent.injection).
    (tmp_path / "rule.md").write_text("rule body")

    compose = _make_compose(
        priorities=["Reliability", "Cleanliness"],
        role_injection="role injection text",
        trait_injections={"foo": "trait body"},
        rules=[ResolvedComponent(name="bar", component_type=ComponentKind.RULE,
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
    # HATS-700: the rule body is read on demand from source_path/rule.md
    # (the composer no longer eager-loads it into ResolvedComponent.injection).
    (tmp_path / "rule.md").write_text("rule body")

    compose = _make_compose(
        priorities=["Reliability"],
        role_injection="base role",
        trait_injections={"foo": "trait body"},
        rules=[ResolvedComponent(name="bar", component_type=ComponentKind.RULE,
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


# ---------- HATS-408 review A1: placeholder expansion ----------


def test_plan_migration_expands_ai_hats_dir_placeholder_in_baseline(tmp_path):
    """v0.6 wrote canonical files with `<ai_hats_dir>` already expanded
    (via expand_path_placeholders). Our baseline render must apply the
    same expansion before diff or every project with a `<ai_hats_dir>`
    token in a trait/role gets falsely classified as user-edited."""
    project = tmp_path / "proj"
    canonical = project / ".agent" / "ai-hats" / "traits"
    canonical.mkdir(parents=True)
    on_disk = canonical.parent / "traits" / "foo.md"
    # Disk file carries the EXPANDED path (this is what v0.6 wrote).
    on_disk.write_text("see .agent/ai-hats/sessions/\n")

    # Composition trait_injection carries the LITERAL placeholder.
    compose = _make_compose(
        trait_injections={"foo": "see <ai_hats_dir>/sessions/"},
    )

    # Without project_dir: false-positive user edit.
    report_unexpanded = m.plan_migration(canonical.parent, compose)
    finding = next(f for f in report_unexpanded.findings if f.path.name == "foo.md")
    assert finding.is_user_edit is True

    # With project_dir: baseline expands, diff matches.
    report_expanded = m.plan_migration(
        canonical.parent, compose, project_dir=project
    )
    finding = next(f for f in report_expanded.findings if f.path.name == "foo.md")
    assert finding.is_user_edit is False, "expanded baseline should match disk bytes"


def test_plan_migration_placeholder_irrelevant_when_token_absent(tmp_path):
    """Smoke: passing project_dir is harmless when no placeholder is present."""
    project = tmp_path / "proj"
    canonical = project / ".agent" / "ai-hats" / "traits"
    canonical.mkdir(parents=True)
    on_disk = canonical.parent / "traits" / "foo.md"
    on_disk.write_text("plain trait body\n")

    compose = _make_compose(trait_injections={"foo": "plain trait body"})
    report = m.plan_migration(canonical.parent, compose, project_dir=project)
    finding = next(f for f in report.findings if f.path.name == "foo.md")
    assert finding.is_user_edit is False


# ---------- HATS-408 review B5: permission-denied resilience ----------


def test_execute_deletions_logs_and_continues_on_permission_error(
    tmp_path, monkeypatch, capsys
):
    """A permission-denied file mid-sweep must not crash the loop.

    HATS-470: error injection moved to safe_delete.shutil.move (the new
    destructive primitive) since execute_deletions now routes through
    safe_delete.discard().
    """
    from ai_hats_core import safe_delete
    safe_delete.reset_session()

    canonical = tmp_path / "ai-hats"
    (canonical / "traits").mkdir(parents=True)
    poison = canonical / "traits" / "poison.md"
    poison.write_text("x\n")
    survivor = canonical / "traits" / "ok.md"
    survivor.write_text("y\n")

    real_move = safe_delete.shutil.move

    def selective_move(src, dst, *args, **kwargs):
        if Path(src).name == "poison.md":
            raise PermissionError(13, "Permission denied", str(src))
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(safe_delete.shutil, "move", selective_move)

    report = m.MigrationReport(findings=[
        m.TierFinding(path=poison, tier=1, kind="trait",
                      is_user_edit=False, baseline_present=True),
        m.TierFinding(path=survivor, tier=1, kind="trait",
                      is_user_edit=False, baseline_present=True),
    ])
    removed = m.execute_deletions(report, canonical)

    # Loop continued: survivor was removed; poison logged.
    assert survivor.absolute() in removed
    assert poison.absolute() not in removed
    captured = capsys.readouterr()
    assert "could not remove" in captured.err
    assert "poison.md" in captured.err
    # poison.md still on disk so the user can fix permissions and re-run.
    assert poison.exists()

    safe_delete.reset_session()


def test_execute_deletions_logs_and_continues_on_rmtree_error(
    tmp_path, monkeypatch, capsys
):
    """Permission-denied DIRECTORY finding must also log + continue.

    HATS-470: safe_delete.discard uses shutil.move for both files and
    dirs (single primitive). Error injection on the move primitive
    covers both branches; the historical rmtree-specific branch test
    becomes another move-injection variant.
    """
    from ai_hats_core import safe_delete
    safe_delete.reset_session()

    canonical = tmp_path / "ai-hats"
    rules_parent = canonical / "library" / "rules"
    rules_parent.mkdir(parents=True)
    poison = rules_parent / "poison_dir"
    poison.mkdir()
    (poison / "rule.md").write_text("x\n")
    survivor_dir = rules_parent / "ok_dir"
    survivor_dir.mkdir()
    (survivor_dir / "rule.md").write_text("y\n")

    real_move = safe_delete.shutil.move

    def selective_move(src, dst, *args, **kwargs):
        if Path(src).name == "poison_dir":
            raise PermissionError(13, "Permission denied", str(src))
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(safe_delete.shutil, "move", selective_move)

    report = m.MigrationReport(findings=[
        m.TierFinding(path=poison, tier=2, kind="lib_rule_dir",
                      is_user_edit=False, baseline_present=True),
        m.TierFinding(path=survivor_dir, tier=2, kind="lib_rule_dir",
                      is_user_edit=False, baseline_present=True),
    ])

    removed = m.execute_deletions(report, canonical)

    assert survivor_dir.absolute() in removed
    assert poison.absolute() not in removed
    assert poison.is_dir()  # untouched
    captured = capsys.readouterr()
    assert "could not remove" in captured.err
    assert "poison_dir" in captured.err

    safe_delete.reset_session()


# ---------- HATS-408 review A4: integration with a real Composer ----------


def test_render_priorities_integrates_with_real_composer(tmp_path):
    """A4: catch drift between our baseline renderer and what a live
    Composer + LibraryResolver actually produce. Uses a hand-built
    minimal library (role + 1 trait + 1 rule) — no ai-hats install
    needed — and asserts the renderer yields non-trivial, well-shaped
    bytes for every Tier-1 kind."""
    from ai_hats.composer import Composer
    from ai_hats.resolver import LibraryResolver, read_rule_body

    # Minimal library layout matching the library/{traits,rules,roles}
    # spec the resolver expects.
    lib = tmp_path / "lib"
    role_dir = lib / "roles" / "minimal"
    rule_dir = lib / "rules" / "demo_rule"
    trait_dir = lib / "traits" / "demo_trait"
    for d in (role_dir, rule_dir, trait_dir):
        d.mkdir(parents=True)

    (role_dir / "config.yaml").write_text(
        "name: minimal\n"
        "injection: role-text-marker\n"
        "priorities:\n  - Reliability\n  - Cleanliness\n"
        "composition:\n"
        "  traits: [demo_trait]\n"
        "  rules: [demo_rule]\n"
        "  skills: []\n"
    )
    (trait_dir / "config.yaml").write_text(
        "name: demo_trait\n"
        "injection: trait-text-marker\n"
        "composition:\n  traits: []\n  rules: []\n  skills: []\n"
    )
    (rule_dir / "config.yaml").write_text("name: demo_rule\n")
    (rule_dir / "rule.md").write_text("rule-text-marker\n")

    resolver = LibraryResolver([lib])
    composer = Composer(resolver)
    result = composer.compose("minimal")

    # Real composer produces what we expect → renderers can build baselines.
    priorities_md = m.render_priorities_md(result.priorities)
    role_md = m.render_role_md(result.role_injection, result.overlay_injection)
    trait_md = m.render_trait_md(result.trait_injections.get("demo_trait", ""))
    # HATS-700: rule body now comes from source_path/rule.md, not injection.
    demo_rule = next(r for r in result.rules if r.name == "demo_rule")
    rule_md = m.render_rule_md(read_rule_body(demo_rule.source_path))

    assert priorities_md is not None
    assert priorities_md.startswith("# Priorities\n")
    assert "Reliability" in priorities_md
    assert role_md == "role-text-marker\n"
    assert trait_md == "trait-text-marker\n"
    assert rule_md == "rule-text-marker\n"

    # Round-trip through plan_migration on disk content that matches
    # baseline → no user edits.
    canonical = tmp_path / "ai-hats"
    (canonical / "traits").mkdir(parents=True)
    (canonical / "rules").mkdir()
    (canonical / "priorities.md").write_text(priorities_md)
    (canonical / "role.md").write_text(role_md)
    (canonical / "traits" / "demo_trait.md").write_text(trait_md)
    (canonical / "rules" / "demo_rule.md").write_text(rule_md)

    report = m.plan_migration(canonical, result)
    assert report.user_edits == [], \
        f"unexpected user-edit findings from real-composer baseline: {report.user_edits}"


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
