"""Phase 1 unit tests for the per-session compose path (HATS-294).

Coverage:
- Fork E: ``ClaudeProvider.build_session_prompt`` is byte-stable across
  consecutive calls for the same role and session_id.
- Fork F: composed default-role prompt content-equivalent to the v0.6
  canonical baseline captured in
  ``tests/fixtures/role_baselines/v06_compose_assistant.md``.
- Cache infra: prompt + plugin live under
  ``<ai_hats_dir>/.cache/sessions/<sid>/``; ``_sweep_orphan_session_caches``
  removes >24h orphans and leaves recent dirs untouched.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.models import ProjectConfig
from ai_hats.paths import session_cache_dir, session_cache_root
from ai_hats.surfaces.claude.provider import ClaudeProvider
from ai_hats.providers import (
    _extract_frontmatter_description,
)
from ai_hats_agy.provider import AgyProvider
from ai_hats.runtime import _cleanup_session_cache, _sweep_orphan_session_caches
from ai_hats.paths import PROJECT_CONFIG


@pytest.fixture
def project_with_library(tmp_path):
    """Minimal library + role for prompt composition."""
    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"

    rule_dir = lib / "rules" / "r"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# Rule body")
    (rule_dir / "metadata.yaml").write_text("name: r\n")

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


# --------------------------------------------------------------------- #
# Fork E — determinism
# --------------------------------------------------------------------- #


def test_build_session_prompt_byte_stable_across_two_calls(project_with_library):
    """Same role + same session_id → byte-identical prompt.md contents.

    Required for Anthropic prompt cache hit rate. If this fails, suspect a
    timestamp / uuid / cwd embedding sneaking into the composition pipeline.
    """
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")
    provider = ClaudeProvider()
    result = asm.composer.compose("test-role")

    args1, _, _ = provider.build_session_prompt(project, result, "stable-sid")
    bytes1 = Path(args1[1]).read_bytes()

    args2, _, _ = provider.build_session_prompt(project, result, "stable-sid")
    bytes2 = Path(args2[1]).read_bytes()

    assert bytes1 == bytes2, "prompt.md must be byte-stable across two calls"


def test_build_session_prompt_byte_stable_distinct_session_ids(project_with_library):
    """Same role, two different session_ids → byte-identical CONTENTS (different paths)."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")
    provider = ClaudeProvider()
    result = asm.composer.compose("test-role")

    args_a, _, _ = provider.build_session_prompt(project, result, "sid-a")
    args_b, _, _ = provider.build_session_prompt(project, result, "sid-b")

    path_a = Path(args_a[1])
    path_b = Path(args_b[1])
    assert path_a != path_b
    assert path_a.read_bytes() == path_b.read_bytes()


# --------------------------------------------------------------------- #
# Fork F — default-path sanity
# --------------------------------------------------------------------- #


_BASELINE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "role_baselines" / "v06_default.md"
)


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
    composed = ClaudeProvider().build_system_prompt(result)

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


def test_build_session_prompt_writes_under_cache_dir(project_with_library):
    """prompt.md and plugin/ live under <ai_hats_dir>/.cache/sessions/<sid>/."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")
    provider = ClaudeProvider()
    result = asm.composer.compose("test-role")

    args, _, _ = provider.build_session_prompt(project, result, "my-sid")
    prompt_path = Path(args[1])
    plugin_idx = args.index("--plugin-dir")
    plugin_path = Path(args[plugin_idx + 1])

    cache_dir = session_cache_dir(project, "my-sid")
    assert prompt_path == cache_dir / "prompt.md"
    assert plugin_path == cache_dir / "plugin"
    assert prompt_path.is_file()
    assert plugin_path.is_dir()


def test_sweep_removes_orphans_older_than_ttl(tmp_path):
    """Orphan session dirs with mtime >24h are removed; recent dirs survive."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)
    root = session_cache_root(project)
    root.mkdir(parents=True)

    fresh = root / "fresh-sid"
    fresh.mkdir()
    (fresh / "prompt.md").write_text("fresh")

    stale = root / "stale-sid"
    stale.mkdir()
    (stale / "prompt.md").write_text("stale")
    # Backdate mtime by 25h.
    past = time.time() - 25 * 3600
    os.utime(stale, (past, past))

    _sweep_orphan_session_caches(project, ttl_hours=24)

    assert fresh.is_dir(), "fresh session dir must survive sweep"
    assert not stale.exists(), "stale session dir must be removed"


def test_sweep_is_idempotent_on_empty_cache_root(tmp_path):
    """No cache root present yet → sweep is a no-op (no crash)."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)
    # No <ai_hats_dir>/.cache/sessions/ exists.
    _sweep_orphan_session_caches(project)  # must not raise


def test_cleanup_session_cache_removes_specific_sid(tmp_path):
    """_cleanup_session_cache drops the named sid dir but leaves others alone."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)
    root = session_cache_root(project)
    root.mkdir(parents=True)

    keep = root / "keep-sid"
    keep.mkdir()
    (keep / "prompt.md").write_text("keep")

    drop = root / "drop-sid"
    drop.mkdir()
    (drop / "prompt.md").write_text("drop")

    _cleanup_session_cache(project, "drop-sid")

    assert keep.is_dir()
    assert not drop.exists()


def test_cleanup_session_cache_is_idempotent(tmp_path):
    """Missing sid dir → cleanup is a no-op."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)
    # No cache root, no sid dir. Must not raise.
    _cleanup_session_cache(project, "does-not-exist")


# --------------------------------------------------------------------- #
# Materialize overwrites existing target (Fork E parity for plugin/)
# --------------------------------------------------------------------- #


def test_build_session_prompt_recovers_from_stale_cache_dir(project_with_library):
    """If <sid>/plugin/ already has stale content from a previous run with
    the same sid (orphan SIGKILL case), build_session_prompt wipes and
    rebuilds it cleanly.
    """
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")
    provider = ClaudeProvider()
    result = asm.composer.compose("test-role")

    # Plant a stale file in the would-be cache dir.
    cache_dir = session_cache_dir(project, "stale-sid")
    cache_dir.mkdir(parents=True)
    plugin_dir = cache_dir / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "leftover.txt").write_text("stale")

    args, _, _ = provider.build_session_prompt(project, result, "stale-sid")
    plugin_idx = args.index("--plugin-dir")
    plugin_path = Path(args[plugin_idx + 1])

    assert plugin_path == plugin_dir
    assert not (plugin_path / "leftover.txt").exists()
    assert (plugin_path / ".claude-plugin" / "plugin.json").exists()


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
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: doc-protocol skill\n---\n# body\n"
    )
    skill = ResolvedComponent(
        name="doc-protocol",
        component_type=ComponentKind.SKILL,
        source_path=skill_dir,
        injection="# body",
    )
    # HATS-700: the always-on body is read on demand from source_path/rule.md.
    rule_dir = tmp_path / "rules" / "dev_rule_tool_call_hygiene"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text(
        "# Rule: Tool-Call Hygiene\nUse dedicated tools over Bash."
    )
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

    claude_prompt = ClaudeProvider().build_system_prompt(result)
    agy_prompt = AgyProvider().build_system_prompt(result)

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


# --------------------------------------------------------------------- #
# HATS-813 — _extract_frontmatter_description now parses real YAML. The
# skill-index description lookup keeps its name fallback and never crashes
# the prompt build on a malformed frontmatter block.
# --------------------------------------------------------------------- #


def _skill_on_disk(tmp_path: Path, name: str, skill_md: str) -> ResolvedComponent:
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_md)
    return ResolvedComponent(
        name=name, component_type=ComponentKind.SKILL, source_path=skill_dir
    )


def test_extract_description_reads_frontmatter(tmp_path):
    skill = _skill_on_disk(
        tmp_path, "doc", "---\ndescription: the doc skill\n---\n# body\n"
    )
    assert _extract_frontmatter_description(skill) == "the doc skill"


def test_extract_description_malformed_warns_then_falls_back(tmp_path, caplog):
    """A broken frontmatter block must not raise on the prompt-build path — but
    the malformed state is logged (observable), NOT silently collapsed into the
    same path as a skill that merely declares no description."""
    skill = _skill_on_disk(tmp_path, "broken", "---\nbad: : indent\n---\nbody\n")
    with caplog.at_level(logging.WARNING, logger="ai_hats.providers"):
        assert _extract_frontmatter_description(skill) == "broken"
    assert "malformed" in caplog.text
    assert "broken" in caplog.text


def test_extract_description_absent_key_is_silent(tmp_path, caplog):
    """The contrast: a valid block with no description falls back to the name
    WITHOUT a warning — only the malformed state is noisy."""
    skill = _skill_on_disk(tmp_path, "quiet", "---\nname: quiet\n---\nbody\n")
    with caplog.at_level(logging.WARNING, logger="ai_hats.providers"):
        assert _extract_frontmatter_description(skill) == "quiet"
    assert caplog.text == ""


def test_extract_description_missing_falls_back_to_name(tmp_path):
    skill = ResolvedComponent(
        name="ghost",
        component_type=ComponentKind.SKILL,
        source_path=tmp_path / "absent",
    )
    assert _extract_frontmatter_description(skill) == "ghost"


def test_build_session_prompt_injects_skill_script_paths_to_env(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
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

    # ClaudeProvider
    claude_p = ClaudeProvider()
    _, claude_env, _ = claude_p.build_session_prompt(project, result, "sid-claude")
    assert "PATH" in claude_env
    assert str(skill_dir / "scripts") in claude_env["PATH"]

    # AgyProvider
    agy_p = AgyProvider()
    _, agy_env, _ = agy_p.build_session_prompt(project, result, "sid-agy")
    assert "PATH" in agy_env
    assert str(skill_dir / "scripts") in agy_env["PATH"]

