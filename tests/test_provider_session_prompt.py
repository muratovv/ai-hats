"""Phase 1 unit tests for the per-session compose path (HATS-294).

Coverage:
- Fork E: the claude session context is byte-stable across consecutive
  plannings for the same role and session root.
- Fork F: composed default-role prompt content-equivalent to the v0.6
  canonical baseline captured in
  ``tests/fixtures/role_baselines/v06_compose_assistant.md``.
- Cache infra: prompt + plugin live under
  ``<cache_root>/sessions/<sid>/``; ``_sweep_orphan_session_caches``
  removes >24h orphans and leaves recent dirs untouched.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import os
import time
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.models import ProjectConfig
from ai_hats.surfaces.claude.provider import ClaudeSurface
from ai_hats.surfaces.agy.provider import AgySurface
from ai_hats.runtime import _cleanup_session_cache, _sweep_orphan_session_caches
from ai_hats.paths import PROJECT_CONFIG
from tests._plan_helpers import composition_of, materialized, planned


@pytest.fixture
def project_with_library(tmp_path):
    """Minimal library + role for prompt composition."""
    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"

    rule_dir = lib / "rules" / "r"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# Rule body")

    skill_dir = lib / "skills" / "s"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: s\ndescription: x\n---\n# body\n")

    trait_dir = lib / "traits" / "t"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text("name: t\ninjection: Trait body.\n")

    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "priorities:\n  - Quality\n"
        "composition:\n  traits: [t]\n  rules: [r]\n  skills: [s]\n"
        "injection: Role body.\n"
    )

    ProjectConfig(provider="claude", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)
    return project, lib


def _composed(project: Path, lib: Path):
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")
    result = asm.composer.compose("test-role")
    return asm, composition_of(result, layout=asm.layout, resolver=asm.resolver)


# --------------------------------------------------------------------- #
# Fork E — determinism
# --------------------------------------------------------------------- #


def test_the_context_is_byte_stable_across_two_plannings(project_with_library):
    """Same role + same session root → the same plan, byte-identical prompt.md.

    Required for Anthropic prompt cache hit rate. If this fails, suspect a
    timestamp / uuid / cwd embedding sneaking into the composition pipeline.
    """
    project, lib = project_with_library
    asm, composition = _composed(project, lib)
    root = asm.layout.cache.session("stable-sid")

    first = materialized(ClaudeSurface(), composition, layout=asm.layout, root=root)
    bytes1 = Path(first.context).read_bytes()
    second = materialized(ClaudeSurface(), composition, layout=asm.layout, root=root)
    bytes2 = Path(second.context).read_bytes()

    assert first == second
    assert bytes1 == bytes2, "prompt.md must be byte-stable across two plannings"


def test_the_context_is_byte_stable_across_distinct_session_roots(project_with_library):
    """Same role, two different session roots → byte-identical CONTENTS (different paths)."""
    project, lib = project_with_library
    asm, composition = _composed(project, lib)

    plan_a = materialized(
        ClaudeSurface(), composition, layout=asm.layout, root=asm.layout.cache.session("sid-a")
    )
    plan_b = materialized(
        ClaudeSurface(), composition, layout=asm.layout, root=asm.layout.cache.session("sid-b")
    )

    assert plan_a.context != plan_b.context
    assert Path(plan_a.context).read_bytes() == Path(plan_b.context).read_bytes()


# --------------------------------------------------------------------- #
# Fork F — default-path sanity
# --------------------------------------------------------------------- #


_BASELINE_FIXTURE = Path(__file__).parent / "fixtures" / "role_baselines" / "v06_default.md"


def test_composed_default_role_covers_canonical_baseline_content(tmp_path):
    """The composed prompt for the project's default role must contain every
    structural signal that v0.6 used to deliver via CLAUDE.md @-import of
    canonical files.

    Spot-check against the captured baseline: priorities words, every trait
    name's injection heading, every always-on rule name, and the role
    injection. This is a content-equivalence test, not a byte-diff —
    rendering differs (composed prompt has ``## PRIORITIES`` heading vs
    ``# Priorities`` in canonical priorities.md), but the *signal* the
    agent picks up must survive the refactor.
    """
    # Use the real project's library (this very repo) for a realistic test.
    repo_root = Path(__file__).parent.parent
    # Compose the project's default-role-equivalent (assistant).
    asm = Assembler(repo_root)
    result = asm.composer.compose("assistant", overlay=asm._get_overlay("assistant"))
    composed = ClaudeSurface().build_system_prompt(result)

    # Signals from the v0.6 baseline that must survive.
    baseline = _BASELINE_FIXTURE.read_text()
    # Priorities words (from priorities.md)
    for word in ("Reliability", "Cleanliness", "Velocity"):
        assert word in baseline, f"baseline regression: {word} not in fixture"
        assert word in composed, f"composed default-role missing priority: {word}"

    # Role injection signal (PRIMARY AUTOMATION ASSISTANT from role.md)
    assert "PRIMARY AUTOMATION ASSISTANT" in baseline
    assert "PRIMARY AUTOMATION ASSISTANT" in composed

    # Always-on rules must appear by name in the composed prompt
    for rule_name in (
        "global_rule_destructive_actions",
        "global_rule_resource_hygiene",
        "dev_rule_secure_coding",
        "dev_rule_tool_call_hygiene",
    ):
        assert rule_name in composed, f"always-on rule missing: {rule_name}"


# --------------------------------------------------------------------- #
# Cache dir + TTL sweep
# --------------------------------------------------------------------- #


def test_the_plan_writes_under_the_session_root(project_with_library):
    """prompt.md and plugin/ live under <cache_root>/sessions/<sid>/."""
    project, lib = project_with_library
    asm, composition = _composed(project, lib)
    cache_dir = asm.layout.cache.session("my-sid")

    plan = materialized(ClaudeSurface(), composition, layout=asm.layout, root=cache_dir)
    args = list(plan.launch.args)
    plugin_path = Path(args[args.index("--plugin-dir") + 1])

    assert plan.context == cache_dir / "prompt.md"
    assert plugin_path == cache_dir / "plugin"
    assert plan.context.is_file()
    assert plugin_path.is_dir()


def test_sweep_removes_orphans_older_than_ttl(tmp_path):
    """Orphan session dirs with mtime >24h are removed; recent dirs survive."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)
    root = ProjectLayout.at(project).cache.sessions
    root.mkdir(parents=True)

    fresh = root / "fresh-sid"
    fresh.mkdir()
    (fresh / "prompt.md").write_text("fresh")

    stale = root / "stale-sid"
    stale.mkdir()
    (stale / "prompt.md").write_text("stale")
    shared_auth = tmp_path / "shared-auth.json"
    shared_auth.write_text("preserve me")
    stale_home = stale / "codex-home"
    stale_home.mkdir()
    (stale_home / "auth.json").symlink_to(shared_auth)
    # Backdate mtime by 25h.
    past = time.time() - 25 * 3600
    os.utime(stale, (past, past))

    _sweep_orphan_session_caches(ProjectLayout.at(project), ttl_hours=24)

    assert fresh.is_dir(), "fresh session dir must survive sweep"
    assert not stale.exists(), "stale session dir must be removed"
    assert shared_auth.read_text() == "preserve me"


def test_sweep_is_idempotent_on_empty_cache_root(tmp_path):
    """No cache root present yet → sweep is a no-op (no crash)."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)
    # No <cache_root>/sessions/ exists.
    _sweep_orphan_session_caches(ProjectLayout.at(project))  # must not raise


def test_cleanup_session_cache_removes_specific_sid(tmp_path):
    """_cleanup_session_cache drops the named sid dir but leaves others alone."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)
    root = ProjectLayout.at(project).cache.sessions
    root.mkdir(parents=True)

    keep = root / "keep-sid"
    keep.mkdir()
    (keep / "prompt.md").write_text("keep")

    drop = root / "drop-sid"
    drop.mkdir()
    (drop / "prompt.md").write_text("drop")

    _cleanup_session_cache(ProjectLayout.at(project).cache.session("drop-sid"))

    assert keep.is_dir()
    assert not drop.exists()


def test_cleanup_session_cache_is_idempotent(tmp_path):
    """Missing sid dir → cleanup is a no-op."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)
    # No cache root, no sid dir. Must not raise.
    _cleanup_session_cache(ProjectLayout.at(project).cache.session("does-not-exist"))


# --------------------------------------------------------------------- #
# HATS-701 — AVAILABLE SKILLS index is provider-specific: Claude omits it
# (skills reach the agent via the native --plugin-dir registry), Agy
# keeps it (no native registry — the index is the only discovery channel).
# --------------------------------------------------------------------- #


def _skill_composition(tmp_path: Path) -> CompositionResult:
    """A CompositionResult carrying one on-disk skill with a frontmatter
    description, so build_system_prompt's index would list it."""
    skill_dir = tmp_path / "skills" / "doc-protocol"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\ndescription: doc-protocol skill\n---\n# body\n")
    skill = ResolvedComponent(
        name="doc-protocol",
        component_type=ComponentKind.SKILL,
        source_path=skill_dir,
        injection="# body",
    )
    # HATS-700: the always-on body is read on demand from source_path/rule.md.
    rule_dir = tmp_path / "rules" / "dev_rule_tool_call_hygiene"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# Rule: Tool-Call Hygiene\nUse dedicated tools over Bash.")
    rule = ResolvedComponent(
        name="dev_rule_tool_call_hygiene",
        component_type=ComponentKind.RULE,
        source_path=rule_dir,
    )
    return CompositionResult(
        name="role",
        priorities=["Reliability"],
        rules=[rule],
        skills=[skill],
        injections=[],
    )


def test_native_registry_providers_omit_skills_index(tmp_path):
    """Skills reach the agent via each provider's native registry, so the
    AVAILABLE SKILLS text-index is a duplicate: Claude --plugin-dir (HATS-701),
    agy .agy/skills/ (HATS-993)."""
    result = _skill_composition(tmp_path)

    claude_prompt = ClaudeSurface().build_system_prompt(result)
    agy_prompt = AgySurface().build_system_prompt(result)

    # The divergence — the core of HATS-701.
    assert "## AVAILABLE SKILLS" not in claude_prompt, (
        "Claude must NOT emit the AVAILABLE SKILLS index — skills reach the "
        "agent via the --plugin-dir native registry. Prompt:\n" + claude_prompt
    )
    # HATS-993: agy joined the native-registry providers (.agy/skills/).
    assert "## AVAILABLE SKILLS" not in agy_prompt, (
        "Agy must NOT emit the AVAILABLE SKILLS index — skills reach the "
        "agent via the native .agy/skills/ registry (HATS-993)."
    )
    # The skill name follows its section: absent from both prompts.
    assert "doc-protocol" not in claude_prompt
    assert "doc-protocol" not in agy_prompt

    # Non-skill sections are unaffected for BOTH providers (shared helper
    # must not drop priorities / always-on rules / their relocation).
    for prompt in (claude_prompt, agy_prompt):
        assert "## PRIORITIES" in prompt
        assert "Reliability" in prompt
        assert "dev_rule_tool_call_hygiene" in prompt
        assert "Tool-Call Hygiene" in prompt


def test_the_plan_puts_the_mirrored_skill_scripts_on_path(tmp_path):
    """The agent calls a skill's scripts by name from the session's own mirror
    — never from the library it was composed from."""
    project = tmp_path / "project"
    project.mkdir()
    layout = ProjectLayout.at(project)
    skill_dir = tmp_path / "skills" / "with-script"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: with-script\n---\n")

    skill = ResolvedComponent(
        name="with-script",
        component_type=ComponentKind.SKILL,
        source_path=skill_dir,
    )
    result = CompositionResult(
        name="role",
        priorities=[],
        rules=[],
        skills=[skill],
        injections=[],
    )
    composition = composition_of(result, layout=layout)

    for surface in (ClaudeSurface(), AgySurface()):
        root = layout.cache.session(f"sid-{surface.name}")
        plan = planned(surface, composition, layout=layout, root=root)
        mirror = surface.session_skills_root(layout, root.name) / "with-script" / "scripts"
        assert plan.env["PATH"].split(os.pathsep)[0] == str(mirror), surface.name
        assert str(skill_dir / "scripts") not in plan.env["PATH"], surface.name
