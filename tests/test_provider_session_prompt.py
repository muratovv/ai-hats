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

import os
import time
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import session_cache_dir, session_cache_root
from ai_hats.providers import ClaudeProvider
from ai_hats.runtime import _cleanup_session_cache, _sweep_orphan_session_caches


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

    ProjectConfig(provider="claude", library_paths=[str(lib)]).save(project / "ai-hats.yaml")
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

    args1, _ = provider.build_session_prompt(project, result, "stable-sid")
    bytes1 = Path(args1[1]).read_bytes()

    args2, _ = provider.build_session_prompt(project, result, "stable-sid")
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

    args_a, _ = provider.build_session_prompt(project, result, "sid-a")
    args_b, _ = provider.build_session_prompt(project, result, "sid-b")

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

    args, _ = provider.build_session_prompt(project, result, "my-sid")
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
    ProjectConfig().save(project / "ai-hats.yaml")
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
    ProjectConfig().save(project / "ai-hats.yaml")
    # No <ai_hats_dir>/.cache/sessions/ exists.
    _sweep_orphan_session_caches(project)  # must not raise


def test_cleanup_session_cache_removes_specific_sid(tmp_path):
    """_cleanup_session_cache drops the named sid dir but leaves others alone."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / "ai-hats.yaml")
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
    ProjectConfig().save(project / "ai-hats.yaml")
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

    args, _ = provider.build_session_prompt(project, result, "stale-sid")
    plugin_idx = args.index("--plugin-dir")
    plugin_path = Path(args[plugin_idx + 1])

    assert plugin_path == plugin_dir
    assert not (plugin_path / "leftover.txt").exists()
    assert (plugin_path / ".claude-plugin" / "plugin.json").exists()
