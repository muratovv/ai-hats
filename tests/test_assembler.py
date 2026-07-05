"""Tests for assembly engine."""

import pytest
from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import (
    AI_HATS_MANAGED_MARKER,
    claude_plugin_manifest,
    claude_skills_dir,
    hooks_dir,
    rules_dir,
    runs_dir,
    skills_dir,
    state_md_path,
    tasks_dir,
)

# HATS-469: ``Assembler.bump()`` was removed; use the test-side pipeline
# helper that mirrors ``cli/assembly.py::do_bump``.
from tests._assembler_helpers import bump_pipeline
from ai_hats.paths import PROJECT_CONFIG


@pytest.fixture
def project_with_library(tmp_path):
    """Create a project directory with a minimal library."""
    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"

    # Rule
    rule_dir = lib / "rules" / "test_rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# Test Rule")
    (rule_dir / "metadata.yaml").write_text("name: test_rule\n")

    # Skill
    skill_dir = lib / "skills" / "test_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Test Skill")

    # Trait
    trait_dir = lib / "traits" / "trait-base"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text("name: trait-base\ninjection: Base.\n")

    # Role A
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text("""
name: test-role
priorities:
  - Quality
composition:
  traits:
    - trait-base
  rules:
    - test_rule
  skills:
    - test_skill
injection: Role injection.
""")

    # Role B (for override tests)
    role_b_dir = lib / "roles" / "other-role"
    role_b_dir.mkdir(parents=True)
    (role_b_dir / "config.yaml").write_text("""
name: other-role
priorities:
  - Speed
composition:
  traits:
    - trait-base
  rules: []
  skills: []
injection: Other role injection.
""")

    # Create project config. HATS-469: seed ``migration_step=latest_step()``
    # so init's ``_refresh`` is silent on stderr (registry no-op). Tests
    # exercising migration registry replay should pre-create yaml with
    # ``migration_step=0`` explicitly (see ``test_bump_persists_*``).
    from ai_hats.migrations import latest_step

    config = ProjectConfig(
        provider="gemini",
        library_paths=[str(lib)],
        migration_step=latest_step(),
    )
    config.save(project / PROJECT_CONFIG)

    return project, lib


def test_init_creates_structure(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)

    asm = Assembler(project)
    asm.init()

    assert (rules_dir(project)).is_dir()
    assert (skills_dir(project)).is_dir()
    assert (hooks_dir(project)).is_dir()
    assert (tasks_dir(project)).is_dir()
    assert (runs_dir(project)).is_dir()
    assert (project / PROJECT_CONFIG).exists()
    assert (state_md_path(project)).exists()


def test_init_is_idempotent(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)

    asm = Assembler(project)
    asm.init()
    asm.init()  # Second call should not fail

    assert (rules_dir(project)).is_dir()


def test_set_role(project_with_library):
    """HATS-407: set_role is runtime-bootstrap — composes, writes active_role,
    ensures provider scaffold, installs hooks, regenerates user-rules
    aggregator. No more _copy_components materialization of rules/skills."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    result = asm.set_role("test-role")

    assert result.name == "test-role"
    assert len(result.errors) == 0
    # Gemini inline path: ./GEMINI.md exists with role injection.
    assert (project / "GEMINI.md").exists()
    prompt = (project / "GEMINI.md").read_text()
    assert "Role injection" in prompt
    # HATS-407: rules/skills are NOT copied into the canonical library tree.
    # Composition resolves them in-memory via the library layers.
    assert not (rules_dir(project) / "test_rule").exists()
    assert not (skills_dir(project) / "test_skill").exists()


def test_set_role_with_claude(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    asm.set_role("test-role", provider_name="claude")
    # HATS-294: ./CLAUDE.md is a thin scaffold importing only user-rules via
    # imports.md. Role injection is composed per-session by the provider, not
    # materialized to disk.
    assert (project / "CLAUDE.md").exists()
    assert "@./.agent/ai-hats/imports.md" in (project / "CLAUDE.md").read_text()
    assert "Role injection" in asm.composer.compose("test-role").role_injection


# HATS-407: rollback / _backup / _restore_backup removed — git is the
# user-facing recovery path. Tests below were retired with the helpers.


def test_status(project_with_library):
    """HATS-407: status.health reflects on-disk artefacts only.
    With per-session compose, rules/skills are NOT materialized into the
    canonical tree, so the only verifiable disk pieces are imports.md and
    the provider system prompt."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    status = asm.status()
    assert status["role"] == "test-role"
    assert status["provider"] == "gemini"
    assert status["tree"] is not None
    assert "imports.md" in status["health"]
    assert status["health"]["imports.md"] == "OK"
    assert status["health"]["system_prompt"] == "OK"


def test_bump(project_with_library):
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    result = bump_pipeline(asm)
    assert result is not None
    assert result.name == "test-role"


def test_bump_pipeline_returns_none_without_role(project_with_library):
    """HATS-469: bump_pipeline must return None when no role is active —
    same contract as the old ``Assembler.bump()`` legacy bare-bump path."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()  # no role
    result = bump_pipeline(asm)
    assert result is None


def test_reinit_without_role_arg_still_composes_for_default_role(project_with_library):
    """HATS-469 R8 (audit regression): re-init without ``-r`` MUST still
    compose for ``default_role`` (from yaml) so role git hooks get
    re-installed. The pre-HATS-469 auto-bump path did this implicitly
    via ``asm.bump()``; with auto-bump gone (R6), init must read the
    saved role explicitly.

    Spy: ``_install_git_hooks`` is the canonical "role pipeline fired"
    signal. We monkeypatch it on the second Assembler to count calls.
    """
    project, lib = project_with_library
    # Make ``.git/`` so _refresh's guard would normally let
    # _install_git_hooks fire.
    (project / ".git").mkdir(exist_ok=True)

    # Step 1: init with explicit role → persists default_role.
    asm = Assembler(project, library_paths=[lib])
    asm.init(role="test-role")
    assert asm.project_config.default_role == "test-role"

    # Step 2: re-init WITHOUT role kwarg on the same project.
    asm2 = Assembler(project, library_paths=[lib])
    install_calls = []
    asm2.hooks.install_git_hooks = lambda r: install_calls.append(r.name)  # type: ignore[assignment]
    asm2.init()  # no role; default_role=test-role in yaml

    assert install_calls == ["test-role"], (
        f"HATS-469 R8: re-init without -r failed to re-install role "
        f"git hooks for default_role. _install_git_hooks calls: "
        f"{install_calls!r}"
    )


# -- HATS-415: bump inline v0.6 → v0.7 migration (replaces HATS-408 naive gate) --


def test_bump_refuses_on_v06_user_edits(project_with_library):
    """v0.6 canonical file on disk whose bytes do not match the composition
    baseline → bump must raise AssemblyError with per-file guidance pointing
    at the v0.7 home. Without this, ``self update`` would silently delete
    the user's edits via ``write_canonical``'s manifest sweep.
    """
    from ai_hats.assembler import AssemblyError

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    canonical = project / ".agent" / "ai-hats"
    # A v0.6 file with content that won't match any baseline → classified
    # as user_edit (conservative — None baseline path or diverging bytes).
    (canonical / "priorities.md").write_text(
        "# Priorities\n\n1. user-edited paragraph that must trigger refusal\n"
    )

    with pytest.raises(AssemblyError) as exc:
        bump_pipeline(asm)
    msg = str(exc.value)
    assert "v0.6 canonical layout detected" in msg
    assert "user edits found on disk" in msg
    # Per-file guidance present.
    assert "priorities.md" in msg
    assert "user-rules" in msg or "library/usage" in msg
    # The user-edited file MUST still be on disk (refusal = no writes).
    assert (canonical / "priorities.md").exists()


def test_bump_force_v07_migration_overwrites_user_edits(project_with_library, capsys):
    """``force_v07_migration=True`` bypasses the refusal — files are swept
    even though the diff classifier flagged them as user-edited. One WARN
    line per overwritten file lands on stderr."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    canonical = project / ".agent" / "ai-hats"
    (canonical / "priorities.md").write_text("# Priorities\n\n1. user-edited\n")

    # No raise — force bypasses the AssemblyError.
    bump_pipeline(asm, force_v07_migration=True)

    assert not (canonical / "priorities.md").exists(), (
        "force_v07_migration must sweep the user-edited file"
    )
    captured = capsys.readouterr()
    assert "WARN: v07-migrate: overwriting" in captured.err
    assert "priorities.md" in captured.err


def test_bump_silent_on_clean_v07_layout(project_with_library):
    """No v0.6 files on disk → migration is a no-op, bump proceeds normally."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    canonical = project / ".agent" / "ai-hats"
    # Sanity: project_with_library + init does not seed any v0.6 Tier-1 files.
    assert not (canonical / "priorities.md").exists()
    assert not (canonical / "role.md").exists()
    bump_pipeline(asm)  # no raise — has_work=False path


def test_run_v07_migration_composes_role_once(project_with_library, monkeypatch):
    """HATS-755: ``_run_v07_migration`` composes the active role exactly once.

    The migration-plan baseline (``compose_for_role`` at the top of the
    method) and the Tier-2 source lookup (``_build_v07_tier2_source_lookup``)
    observe identical state — back-to-back, nothing mutates compose inputs
    between them. The lookup therefore reuses the already-computed
    composition instead of recomposing the same role. Reverting the collapse
    restores the second compose → count == 2 → this test fails.
    """
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    import ai_hats.assembler as assembler_mod

    real_compose = assembler_mod.compose_for_role
    roles_composed: list[str] = []

    def counting_compose(assembler, role):
        roles_composed.append(role)
        return real_compose(assembler, role)

    # Both call sites in _run_v07_migration use the bare module-level
    # ``compose_for_role`` name, so one patch intercepts both.
    monkeypatch.setattr(assembler_mod, "compose_for_role", counting_compose)

    asm._run_v07_migration(force=False, check_branches=False)

    assert roles_composed == ["test-role"], (
        "HATS-755: _run_v07_migration must compose the role once; "
        f"got {len(roles_composed)} composes: {roles_composed}"
    )


# -- HATS-413: bump persists yaml hardening (heal + deprecated-strip) --


def test_bump_persists_default_role_heal(project_with_library):
    """The ``default_role := active_role`` heal from from_yaml stays in memory
    by design. ``bump()`` must persist it so the WARN doesn't re-fire on
    every subsequent CLI invocation."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    # Write a yaml that triggers the heal: active_role set + default_role empty.
    config_path = project / PROJECT_CONFIG
    config_path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: test-role\n"
        "default_role: ''\n"
    )

    # Reload through Assembler (triggers from_yaml heal in memory).
    asm2 = Assembler(project, library_paths=[lib])
    assert asm2.project_config.default_role == "test-role"  # healed in memory

    bump_pipeline(asm2)

    # Yaml on disk now has the healed value persisted.
    import yaml as _yaml

    saved = _yaml.safe_load(config_path.read_text())
    assert saved["default_role"] == "test-role"

    # Confirm the next load doesn't trigger another heal (raw == in-memory).
    asm3 = Assembler(project, library_paths=[lib])
    raw = _yaml.safe_load(config_path.read_text())
    assert raw.get("default_role") == asm3.project_config.default_role == "test-role"


def test_bump_persists_deprecated_field_strip(project_with_library):
    """HATS-413/471/469: deprecated yaml keys are stripped on the FIRST
    install-time pass through the registry (step=1 ``_normalize_yaml``).

    HATS-469 unification means ``init`` itself runs the registry, so any
    deprecated field present on the pre-init yaml is cleaned during the
    init call. After ``migration_step`` advances past step=1,
    ``_normalize_yaml`` is a one-shot and does NOT re-fire on subsequent
    ``bump_pipeline`` calls (HATS-471 registry contract). Re-introduced
    deprecated keys are warned about on every load but not
    auto-persisted.

    The fixture seeds ``migration_step=latest_step()``; this test
    explicitly rewinds it to 0 to trigger the registry replay.
    """
    import yaml as _yaml

    project, lib = project_with_library
    config_path = project / PROJECT_CONFIG
    # Rewind migration_step to 0 + add deprecated ghost BEFORE init so
    # the first registry pass picks it up.
    raw = _yaml.safe_load(config_path.read_text())
    raw["migration_step"] = 0
    raw["imports_order"] = "role-first"
    config_path.write_text(_yaml.safe_dump(raw))

    asm = Assembler(project, library_paths=[lib])
    asm.init()  # _refresh(install_time=True) → run_pending → step=1 cleans

    saved = _yaml.safe_load(config_path.read_text())
    assert "imports_order" not in saved


def test_bump_no_op_when_yaml_already_normalized(project_with_library):
    """Idempotency: bump on a fully-normalized yaml does NOT rewrite the file
    (bytes preserved). HATS-471: ``project_with_library`` writes yaml with
    ``migration_step=0`` (pre-HATS-471 shape), so the FIRST bump after
    ``init`` legitimately advances the registry counter to ``latest_step``
    and persists. The subsequent bump is the true no-op tested here.
    """
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    config_path = project / PROJECT_CONFIG

    # First bump: drives migration_step → latest_step via the registry runner,
    # plus any HATS-413 yaml heal still pending from the fixture's bare save.
    bump_pipeline(asm)

    # Snapshot AFTER the first bump has done its one-time normalization.
    pre_bytes = config_path.read_bytes()
    pre_mtime = config_path.stat().st_mtime_ns

    bump_pipeline(asm)

    # Bytes unchanged → no rewrite happened on the second bump.
    # (mtime can move if other init paths touch the file; bytes is the
    # ground-truth contract.)
    assert config_path.read_bytes() == pre_bytes, (
        f"yaml rewritten unexpectedly:\nbefore mtime={pre_mtime}, "
        f"after mtime={config_path.stat().st_mtime_ns}"
    )


def test_bump_skips_normalize_when_v07_migration_refuses(project_with_library):
    """HATS-415 ordering contract: ``_run_v07_migration`` is the first action
    inside ``bump()``. When user edits trigger a refusal, ``_normalize_yaml``
    must NOT have run — the v0.6 yaml shape is preserved until the user
    resolves the conflict (relocates content, or re-runs with
    ``force_v07_migration=True``)."""
    from ai_hats.assembler import AssemblyError

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    config_path = project / PROJECT_CONFIG
    # Re-write with v0.6-shape yaml (deprecated field + empty default_role).
    config_path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: test-role\n"
        "default_role: ''\n"
        "imports_order: role-first\n"
    )
    canonical = project / ".agent" / "ai-hats"
    (canonical / "priorities.md").write_text("# Priorities\n\n1. user-edited\n")
    pre_bytes = config_path.read_bytes()

    asm2 = Assembler(project, library_paths=[lib])
    with pytest.raises(AssemblyError):
        bump_pipeline(asm2)

    # Yaml untouched — migration refusal fired before _normalize_yaml.
    assert config_path.read_bytes() == pre_bytes


def test_set_role_then_switch_provider(project_with_library):
    """Switching provider must regenerate system prompt for the new provider.

    Regression: setting role with gemini created GEMINI.md, then wrapping
    with claude found no CLAUDE.md — agent saw no instructions.
    """
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    # Set role for gemini (default)
    asm.set_role("test-role")
    assert (project / "GEMINI.md").exists()
    assert "Role injection" in (project / "GEMINI.md").read_text()

    # Now switch to claude — scaffold + .claude/CLAUDE.md aggregator carry
    # the role content (HATS-284/285).
    asm.set_role("test-role", provider_name="claude")
    assert (project / "CLAUDE.md").exists()
    assert "@./.agent/ai-hats/imports.md" in (project / "CLAUDE.md").read_text()
    assert "Role injection" in asm.composer.compose("test-role").role_injection

    # Profile must track the new provider
    from ai_hats.models import ProjectConfig

    profile = ProjectConfig.from_yaml(project / PROJECT_CONFIG)
    assert profile.provider == "claude"
    assert profile.active_role == "test-role"


def test_wrap_reassembles_on_provider_mismatch(project_with_library):
    """WrapRunner must auto-reassemble when provider differs from profile.

    Scenario: role set with gemini, then `ai-hats wrap claude` — should
    automatically rebuild CLAUDE.md before launching.
    """
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")  # provider=gemini

    # Simulate what WrapRunner.run() does on provider mismatch
    from ai_hats.models import ProjectConfig

    profile = ProjectConfig.from_yaml(project / PROJECT_CONFIG)
    assert profile.provider == "gemini"

    target_provider = "claude"
    if profile.active_role and profile.provider != target_provider:
        asm.set_role(profile.active_role, provider_name=target_provider)

    # CLAUDE.md is the scaffold; role content is in .agent/ai-hats/role.md
    # and surfaced via the .claude/CLAUDE.md aggregator (HATS-284/285).
    assert (project / "CLAUDE.md").exists()
    assert "@./.agent/ai-hats/imports.md" in (project / "CLAUDE.md").read_text()
    assert "Role injection" in asm.composer.compose("test-role").role_injection

    # Profile updated
    profile = ProjectConfig.from_yaml(project / PROJECT_CONFIG)
    assert profile.provider == "claude"


def test_wrap_uses_default_role_when_no_active_role(project_with_library):
    """When no role is set but default_role is configured, wrap should apply it."""
    project, lib = project_with_library
    # Set default_role in config
    from ai_hats.models import ProjectConfig

    config = ProjectConfig.from_yaml(project / PROJECT_CONFIG)
    config.default_role = "test-role"
    config.save(project / PROJECT_CONFIG)

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    # Don't call set_role — simulate fresh project with only default_role

    from ai_hats.models import ProjectConfig

    profile = ProjectConfig.from_yaml(project / PROJECT_CONFIG)
    assert profile.active_role == ""  # No role set yet

    # Simulate what WrapRunner does: pick default_role
    effective_role = profile.active_role or config.default_role
    assert effective_role == "test-role"

    # Apply it (as WrapRunner would)
    asm.set_role(effective_role, provider_name="claude")
    assert (project / "CLAUDE.md").exists()
    assert "@./.agent/ai-hats/imports.md" in (project / "CLAUDE.md").read_text()
    assert "Role injection" in asm.composer.compose("test-role").role_injection


def test_preserve_local_rules(project_with_library):
    """Project-local rules should survive role reassembly."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    # Add a project-local rule
    local_rule = rules_dir(project) / "my_local_rule"
    local_rule.mkdir(parents=True)
    (local_rule / "rule.md").write_text("# My Local Rule")

    # Re-set role
    asm.set_role("test-role")

    # Local rule should still exist
    assert (rules_dir(project) / "my_local_rule" / "rule.md").exists()


# HATS-407: rollback / _backup / _restore_backup helpers removed.
# test_rollback_restores_previous_role, test_rollback_cleans_up_backup_dir,
# test_rollback_returns_false_when_no_backup retired with them.


def test_claude_build_session_prompt_creates_temp_file(project_with_library):
    """ClaudeProvider.build_session_prompt() creates temp file with override prompt."""
    from ai_hats.providers import ClaudeProvider

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    # Set base role so CLAUDE.md exists with project content
    asm.set_role("test-role", provider_name="claude")
    # Add project-local content after markers
    claude_md = project / "CLAUDE.md"
    existing = claude_md.read_text()
    claude_md.write_text(existing + "\n# My Project Rules\nDo stuff.\n")

    # Build override for other-role
    provider = ClaudeProvider()
    result = asm.composer.compose("other-role")
    args, env, _ = provider.build_session_prompt(project, result, "test-sid")

    # HATS-307: args now include --system-prompt-file AND --plugin-dir
    assert args[0] == "--system-prompt-file"
    assert "--plugin-dir" in args
    override_path = Path(args[1])
    plugin_dir = Path(args[args.index("--plugin-dir") + 1])
    assert override_path.exists()
    assert plugin_dir.is_dir()

    content = override_path.read_text()
    # Override prompt is injected
    assert "Other role injection" in content
    # Project-local content is preserved
    assert "My Project Rules" in content
    # Original role injection is NOT present
    assert "Role injection." not in content

    # Cleanup
    override_path.unlink()
    import shutil as _shutil

    _shutil.rmtree(plugin_dir, ignore_errors=True)


def test_claude_build_session_prompt_materializes_role_skills_in_plugin_dir(project_with_library):
    """HATS-307: spawned role's skills must end up under --plugin-dir/skills/."""
    import shutil as _shutil
    from ai_hats.providers import ClaudeProvider

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    # Active role has NO skills (other-role); the spawned role (test-role)
    # composes test_skill — exactly the HATS-307 scenario.
    asm.set_role("other-role", provider_name="claude")

    provider = ClaudeProvider()
    result = asm.composer.compose("test-role")
    args, _, _ = provider.build_session_prompt(project, result, "test-sid")

    assert "--plugin-dir" in args
    plugin_dir = Path(args[args.index("--plugin-dir") + 1])
    try:
        assert claude_plugin_manifest(plugin_dir).exists()
        assert (plugin_dir / "skills" / "test_skill" / "SKILL.md").exists()
    finally:
        Path(args[1]).unlink()
        _shutil.rmtree(plugin_dir, ignore_errors=True)


def test_claude_build_session_prompt_does_not_modify_project_claude_md(project_with_library):
    """build_session_prompt() must never modify the project CLAUDE.md."""
    from ai_hats.providers import ClaudeProvider

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")

    original_content = (project / "CLAUDE.md").read_text()

    provider = ClaudeProvider()
    result = asm.composer.compose("other-role")
    args, _, _ = provider.build_session_prompt(project, result, "test-sid")

    # CLAUDE.md unchanged
    assert (project / "CLAUDE.md").read_text() == original_content
    # Cleanup
    Path(args[1]).unlink()
    if "--plugin-dir" in args:
        import shutil as _shutil

        _shutil.rmtree(args[args.index("--plugin-dir") + 1], ignore_errors=True)


def test_gemini_build_session_prompt_creates_rules_dir(project_with_library):
    """GeminiProvider.build_session_prompt() creates session rules dir with override."""
    import shutil
    from ai_hats.providers import GeminiProvider

    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")  # sets up .agent/rules/

    provider = GeminiProvider()
    result = asm.composer.compose("other-role")
    args, env, _ = provider.build_session_prompt(project, result, "test-sid")

    assert args == []
    assert "GEMINI_CLI_PROJECT_RULES_PATH" in env
    rules_dir = Path(env["GEMINI_CLI_PROJECT_RULES_PATH"])
    assert rules_dir.exists()

    # Mandatory role file exists
    mandatory = rules_dir / "00_MANDATORY_ROLE.md"
    assert mandatory.exists()
    assert "Other role injection" in mandatory.read_text()

    # HATS-407: library-sourced rules are no longer materialized under
    # `<ai_hats_dir>/library/rules/`, so the per-session rules-dir copy
    # of test_rule is gone. Always-on rules + role injection still reach
    # the agent via 00_MANDATORY_ROLE.md (the inline composed prompt).
    assert not (rules_dir / "test_rule").exists()

    # GEMINI.md not touched
    assert "Role injection" in (project / "GEMINI.md").read_text()

    # Cleanup
    shutil.rmtree(rules_dir)


# HATS-407: _backup() removed — test_backup_survives_self_referential_symlinks_in_provider_skills retired.


# --------------------------------------------------------------------- #
# HATS-317 — one-shot .gitignore entry (replaces dynamic managed block)
# --------------------------------------------------------------------- #


def test_gitignore_init_writes_single_line(project_with_library):
    """`init` ensures `.agent/ai-hats/` is in .gitignore. No managed block."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    content = (project / ".gitignore").read_text()
    assert ".agent/ai-hats/" in content
    assert "AI-HATS:START" not in content
    assert "AI-HATS:END" not in content


def test_gitignore_init_idempotent(project_with_library):
    """Re-running `init` doesn't duplicate the .gitignore line."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    first = (project / ".gitignore").read_text()
    asm.init()
    second = (project / ".gitignore").read_text()
    assert first == second
    assert first.count(".agent/ai-hats/") == 1


def test_gitignore_init_preserves_user_content(project_with_library):
    """User lines remain; the ai-hats entry is appended without disturbing them."""
    project, lib = project_with_library
    (project / ".gitignore").write_text("# user header\n*.pyc\nbuild/\n")

    asm = Assembler(project, library_paths=[lib])
    asm.init()

    content = (project / ".gitignore").read_text()
    for fragment in ("# user header", "*.pyc", "build/", ".agent/ai-hats/"):
        assert fragment in content


def test_gitignore_set_role_does_not_touch_gitignore(project_with_library):
    """`set_role` is a no-op on .gitignore (HATS-317 removed the dynamic block)."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    before = (project / ".gitignore").read_text()
    asm.set_role("test-role", provider_name="claude")
    after = (project / ".gitignore").read_text()
    assert before == after


def test_gitignore_opt_out_skips_write(project_with_library):
    """`manage_gitignore=false` keeps init from writing any line."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.project_config.manage_gitignore = False
    asm.init()
    gi = project / ".gitignore"
    if gi.exists():
        assert ".agent/ai-hats/" not in gi.read_text()
    else:
        # No .gitignore created when opt-out is set on a clean project.
        assert True


# --------------------------------------------------------------------- #
# HATS-317 cleanup — legacy managed block sweep on bump
# --------------------------------------------------------------------- #

# Synthetic legacy block — mirrors the pre-HATS-317 generator output:
# per-component lines pointing at canonical-materialized files that
# HATS-294 stopped emitting in v0.7.
_LEGACY_BLOCK = (
    "# AI-HATS:START — managed by ai-hats, do not edit\n"
    ".agent/ai-hats/.last_backup\n"
    ".agent/ai-hats/imports.md\n"
    ".agent/ai-hats/library/skills/audit-reviewer/\n"
    ".agent/ai-hats/rules/dev_rule_edit_efficiency.md\n"
    ".agent/ai-hats/traits/dev::python.md\n"
    "# AI-HATS:END\n"
)


def test_strip_legacy_managed_block_removes_block(project_with_library):
    """HATS-317 cleanup: the legacy `# AI-HATS:START..END` block is removed,
    user content around it is preserved verbatim."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    gi = project / ".gitignore"
    gi.write_text("# user header\n*.pyc\n.agent/\n\n" + _LEGACY_BLOCK + "user-tail\n")

    changed = asm._strip_legacy_managed_block()

    assert changed is True
    content = gi.read_text()
    assert "AI-HATS:START" not in content
    assert "AI-HATS:END" not in content
    # Stale per-component entries gone.
    assert "dev_rule_edit_efficiency" not in content
    assert "dev::python.md" not in content
    assert "audit-reviewer" not in content
    # User content intact.
    for fragment in ("# user header", "*.pyc", ".agent/", "user-tail"):
        assert fragment in content


def test_strip_legacy_managed_block_idempotent(project_with_library):
    """Re-running the sweep on an already-clean file is a no-op."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    gi = project / ".gitignore"
    gi.write_text("*.pyc\n\n" + _LEGACY_BLOCK)

    changed1 = asm._strip_legacy_managed_block()
    assert changed1 is True
    first = gi.read_text()

    changed2 = asm._strip_legacy_managed_block()
    assert changed2 is False
    assert gi.read_text() == first
    assert "AI-HATS:" not in first


def test_strip_legacy_managed_block_no_op_when_absent(project_with_library):
    """A .gitignore without the legacy block is not touched."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    gi = project / ".gitignore"
    original = gi.read_text()

    changed = asm._strip_legacy_managed_block()

    assert changed is False
    assert gi.read_text() == original


def test_strip_legacy_managed_block_respects_opt_out(project_with_library):
    """When manage_gitignore=False the legacy block is NOT stripped —
    opted-out projects own their .gitignore entirely."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.project_config.manage_gitignore = False
    gi = project / ".gitignore"
    gi.write_text(_LEGACY_BLOCK)

    changed = asm._strip_legacy_managed_block()

    assert changed is False
    assert "AI-HATS:START" in gi.read_text()


def test_strip_legacy_managed_block_unclosed_marker_left_alone(project_with_library):
    """Unclosed START marker (corrupted file) → silent no-op.
    Prevents silent destruction of legitimate user content past the opener."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    gi = project / ".gitignore"
    corrupted = "*.pyc\n# AI-HATS:START\n.agent/ai-hats/imports.md\nuser-tail\n"
    gi.write_text(corrupted)

    changed = asm._strip_legacy_managed_block()

    assert changed is False
    assert gi.read_text() == corrupted


# --- HATS-465: orphan user-level `.claude/skills/.ai-hats-managed` ---


def _seed_orphan_marker(fake_home):
    """Create `~/.claude/skills/.ai-hats-managed` under a fake HOME."""
    skills_dir = claude_skills_dir(fake_home)
    skills_dir.mkdir(parents=True)
    (skills_dir / ".ai-hats-managed").write_text("audit-reviewer\nbacklog-manager\n")


def test_warn_orphan_user_level_managed_skills_emits_warn(
    project_with_library, tmp_path, monkeypatch, capsys
):
    """HATS-465: orphan marker under HOME → WARN on stderr with safe-remove hint.

    ai-hats has never written to ``~/.claude/skills/`` (pre-HATS-294 export
    was project-level; HATS-294 dropped permanent export entirely). The
    marker is invariably an artefact of a manual
    ``cp -r .claude/skills/ ~/.claude/skills/``. Bump must surface it
    without self-healing."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr("ai_hats.assembler.Path.home", lambda: fake_home)
    _seed_orphan_marker(fake_home)

    emitted = asm._warn_orphan_user_level_managed_skills()

    assert emitted is True
    err = capsys.readouterr().err
    assert "Orphan ai-hats marker" in err
    assert "~/.claude/skills/.ai-hats-managed" in err
    assert "rm -rf ~/.claude/skills/" in err
    # Marker MUST still exist — we never delete user data ourselves.
    assert (claude_skills_dir(fake_home) / AI_HATS_MANAGED_MARKER).exists()


def test_warn_orphan_user_level_managed_skills_silent_when_absent(
    project_with_library, tmp_path, monkeypatch, capsys
):
    """No marker under HOME → no WARN, returns False."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr("ai_hats.assembler.Path.home", lambda: fake_home)

    emitted = asm._warn_orphan_user_level_managed_skills()

    assert emitted is False
    assert capsys.readouterr().err == ""


def test_warn_orphan_user_level_managed_skills_idempotent(
    project_with_library, tmp_path, monkeypatch, capsys
):
    """Re-running emits WARN every time — non-self-healing by design.

    The fix is user-side (`rm -rf ~/.claude/skills/`); until they do,
    every bump re-surfaces it."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr("ai_hats.assembler.Path.home", lambda: fake_home)
    _seed_orphan_marker(fake_home)

    assert asm._warn_orphan_user_level_managed_skills() is True
    capsys.readouterr()  # drain
    assert asm._warn_orphan_user_level_managed_skills() is True
    assert "Orphan ai-hats marker" in capsys.readouterr().err


def _seed_legacy_skills_mirror(project):
    """Create a pre-HATS-294 `.claude/skills/` mirror: marker + 2 managed
    skill dirs + 1 user-authored dir NOT listed in the marker."""
    skills = claude_skills_dir(project)
    for name in ("audit-reviewer", "backlog-manager"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text("# stale export")
    (skills / "my-own-skill").mkdir()
    (skills / "my-own-skill" / "SKILL.md").write_text("# user-authored")
    (skills / ".ai-hats-managed").write_text("audit-reviewer\nbacklog-manager\n")
    return skills


def test_cleanup_legacy_claude_publish_drops_skills_mirror(project_with_library):
    """Wiring: the heal-path cleanup reaches the mirror, so existing installs
    self-heal on their next `self bump` / `self init` / `self update`."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    skills = _seed_legacy_skills_mirror(project)

    asm._cleanup_legacy_claude_publish()

    assert not (skills / ".ai-hats-managed").exists()
    assert not (skills / "audit-reviewer").exists()
    assert (skills / "my-own-skill" / "SKILL.md").exists()


def test_bump_invokes_legacy_block_sweep(project_with_library):
    """Integration: bump_pipeline chains the sweep so existing users get
    the fix on their next ``ai-hats self bump`` / ``self update``.

    HATS-471: ``_strip_legacy_managed_block`` is registry step=2 — one-shot
    per project, gated by ``migration_step``. The fixture seeds
    ``migration_step=latest`` (post-HATS-469), so this test explicitly
    rewinds it to 0 to trigger the sweep replay.
    """
    import yaml as _yaml

    project, lib = project_with_library
    config_path = project / PROJECT_CONFIG
    raw = _yaml.safe_load(config_path.read_text())
    raw["migration_step"] = 0
    config_path.write_text(_yaml.safe_dump(raw))

    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")
    gi = project / ".gitignore"
    gi.write_text(gi.read_text() + "\n" + _LEGACY_BLOCK)

    # Rewind again — init/set_role may have advanced migration_step.
    raw = _yaml.safe_load(config_path.read_text())
    raw["migration_step"] = 1  # past step=1 (yaml normalize), still before step=2
    config_path.write_text(_yaml.safe_dump(raw))

    asm2 = Assembler(project, library_paths=[lib])
    bump_pipeline(asm2)

    after = gi.read_text()
    assert "AI-HATS:START" not in after
    assert "AI-HATS:END" not in after


# --------------------------------------------------------------------- #
# HATS-155 — manifest-driven .agent/{hooks,skills}/ management
# --------------------------------------------------------------------- #


# HATS-407: _copy_components removed — set_role no longer materializes
# rules/skills/hooks under the canonical library tree. Library overlay tests
# (managed-manifest, library-sourced skill survival) are retired; per-session
# compose resolves these in memory.


def test_managed_manifest_absent_after_set_role(project_with_library):
    """HATS-407: with _copy_components removed, set_role no longer writes
    .ai-hats-managed for skills/hooks/rules — composition resolves them in
    memory via library_paths and the canonical tree stays minimal."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    assert not (skills_dir(project) / ".ai-hats-managed").exists()
    assert not (hooks_dir(project) / ".ai-hats-managed").exists()


def test_user_hook_survives_bump(project_with_library):
    """User-authored file in .agent/hooks/ must not be wiped by re-assembly."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    # Ensure the dir exists (post-HATS-407 it may be empty until the user
    # drops a file in it — _copy_components no longer pre-populates).
    hooks_dir(project).mkdir(parents=True, exist_ok=True)
    user_hook = hooks_dir(project) / "my-custom.sh"
    user_hook.write_text("#!/usr/bin/env bash\necho custom\n")

    bump_pipeline(asm)

    assert user_hook.exists(), "User hook must survive bump"
    assert user_hook.read_text() == "#!/usr/bin/env bash\necho custom\n"


def test_user_skill_dir_survives_bump(project_with_library):
    """User-authored subdir in .agent/skills/ must not be wiped by re-assembly."""
    project, lib = project_with_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("test-role")

    skills_dir(project).mkdir(parents=True, exist_ok=True)
    user_skill = skills_dir(project) / "my_local_skill"
    user_skill.mkdir()
    (user_skill / "SKILL.md").write_text("# local\n")

    bump_pipeline(asm)

    assert (user_skill / "SKILL.md").exists()
    # HATS-407: library-sourced skills are no longer copied into the
    # canonical tree. They are resolved in-memory at session-compose time.
    assert not (skills_dir(project) / "test_skill").exists()


# --------------------------------------------------------------------- #
# HATS-251 — dev_rule_tool_call_hygiene wired as always-on
# --------------------------------------------------------------------- #


def test_tool_call_hygiene_is_always_on(tmp_path):
    """dev_rule_tool_call_hygiene must appear in system prompt (HATS-251)."""
    from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
    from ai_hats.providers import ALWAYS_ON_RULES, ClaudeProvider

    assert "dev_rule_tool_call_hygiene" in ALWAYS_ON_RULES

    # HATS-700: the always-on body is read on demand from source_path/rule.md.
    rule_dir = tmp_path / "dev_rule_tool_call_hygiene"
    rule_dir.mkdir()
    (rule_dir / "rule.md").write_text("# Rule: Tool-Call Hygiene\nUse dedicated tools over Bash.")
    rule = ResolvedComponent(
        name="dev_rule_tool_call_hygiene",
        component_type=ComponentKind.RULE,
        source_path=rule_dir,
    )
    result = CompositionResult(
        name="test",
        priorities=[],
        rules=[rule],
        skills=[],
        injections=[],
    )
    prompt = ClaudeProvider().build_system_prompt(result)
    assert "dev_rule_tool_call_hygiene" in prompt
    assert "Tool-Call Hygiene" in prompt


# --------------------------------------------------------------------- #
# HATS-380 — `<ai_hats_dir>` placeholder must be expanded before reaching
# the agent (skill bodies, rule bodies, role/trait injection).
# --------------------------------------------------------------------- #


@pytest.fixture
def project_with_placeholder_library(tmp_path):
    """Library where every injectable surface embeds `<ai_hats_dir>`."""
    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"

    rule_dir = lib / "rules" / "ph_rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("Rule body refs <ai_hats_dir>/state.\n")
    (rule_dir / "metadata.yaml").write_text("name: ph_rule\n")

    skill_dir = lib / "skills" / "ph_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: ph_skill\ndescription: ph\n---\n"
        "Write reports to <ai_hats_dir>/sessions/retros/.\n"
    )

    trait_dir = lib / "traits" / "trait-ph"
    trait_dir.mkdir(parents=True)
    (trait_dir / "config.yaml").write_text(
        "name: trait-ph\ninjection: 'Trait sees <ai_hats_dir>/tracker.'\n"
    )

    role_dir = lib / "roles" / "ph-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: ph-role\n"
        "priorities: [Quality]\n"
        "composition:\n"
        "  traits: [trait-ph]\n"
        "  rules: [ph_rule]\n"
        "  skills: [ph_skill]\n"
        "injection: 'Role writes to <ai_hats_dir>/sessions/audits/.'\n"
    )

    config = ProjectConfig(provider="claude", library_paths=[str(lib)])
    config.save(project / PROJECT_CONFIG)
    return project, lib


def _assert_no_literal_placeholder(*paths: Path) -> None:
    offenders = [p for p in paths if p.is_file() and "<ai_hats_dir>" in p.read_text()]
    assert not offenders, f"placeholder leaked into: {offenders}"


def test_canonical_dir_has_no_literal_placeholder(project_with_placeholder_library):
    """HATS-294: only ``imports.md`` is materialized on disk; it imports
    user-rules only and must be placeholder-free. Framework content with
    placeholders is composed per-session — verified separately via the
    Provider.build_session_prompt path.
    """
    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("ph-role", provider_name="claude")

    canonical = project / ".agent" / "ai-hats"
    _assert_no_literal_placeholder(canonical / "imports.md")


def test_gemini_inline_prompt_has_no_literal_placeholder(
    project_with_placeholder_library,
):
    """`./GEMINI.md` injection block (Gemini's set_role path) must be expanded."""
    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("ph-role", provider_name="gemini")

    gemini_md = project / "GEMINI.md"
    assert gemini_md.exists()
    content = gemini_md.read_text()
    assert "<ai_hats_dir>" not in content
    # Spot-check substitution landed in the injected block.
    assert ".agent/ai-hats/sessions/audits/" in content


def test_gemini_build_session_prompt_has_no_literal_placeholder(
    project_with_placeholder_library,
):
    """Gemini override prompt (`00_MANDATORY_ROLE.md`) must be expanded."""
    from ai_hats.composer import Composer
    from ai_hats.providers import GeminiProvider
    from ai_hats.resolver import LibraryResolver

    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    result = Composer(LibraryResolver([lib])).compose("ph-role")

    _, env, _ = GeminiProvider().build_session_prompt(project, result, "test-sid")
    override = Path(env["GEMINI_CLI_PROJECT_RULES_PATH"]) / "00_MANDATORY_ROLE.md"
    content = override.read_text()
    assert "<ai_hats_dir>" not in content
    assert ".agent/ai-hats/sessions/audits/" in content


def test_claude_build_session_prompt_has_no_literal_placeholder(
    project_with_placeholder_library,
):
    """Claude --system-prompt-file content must be expanded."""
    from ai_hats.composer import Composer
    from ai_hats.providers import ClaudeProvider
    from ai_hats.resolver import LibraryResolver

    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("ph-role", provider_name="claude")
    result = Composer(LibraryResolver([lib])).compose("ph-role")

    args, _, _ = ClaudeProvider().build_session_prompt(project, result, "test-sid")
    # build_session_prompt returns ["--system-prompt-file", <path>]
    prompt_file = Path(args[args.index("--system-prompt-file") + 1])
    content = prompt_file.read_text()
    assert "<ai_hats_dir>" not in content
    assert ".agent/ai-hats/sessions/audits/" in content


def _subagent_payload(result):
    """Minimal payload for SubAgentRunner helper-method seams (HATS-865)."""
    from ai_hats.composition_payload import CompositionPayload
    from ai_hats.providers import get_provider

    return CompositionPayload(
        result=result, provider=get_provider("claude"), effective_role=result.name,
    )

def test_subagent_meta_prompt_has_no_literal_placeholder(
    project_with_placeholder_library,
):
    """HATS-380 residual gap: SubAgentRunner._build_meta_prompt must expand
    `<ai_hats_dir>` in result.merged_injection. Roles like session-reviewer
    (auto-spawned by reflect-session) carry the literal in their injection."""
    from ai_hats.providers import get_provider
    from ai_hats.runtime import SubAgentRunner

    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("ph-role", provider_name="claude")
    result = asm.composer.compose("ph-role")

    from ai_hats.observe import SessionManager

    runner = SubAgentRunner(
        project,
        _subagent_payload(result),
        session_mgr=SessionManager(project, runs_dir=runs_dir(project)),
    )
    meta_prompt = runner._build_meta_prompt(
        result=result,
        provider=get_provider("claude"),
        task="",
        ticket_id="",
    )

    assert "<ai_hats_dir>" not in meta_prompt
    # Spot-check both trait + role injection landed expanded.
    assert ".agent/ai-hats/tracker" in meta_prompt
    assert ".agent/ai-hats/sessions/audits/" in meta_prompt


def test_subagent_meta_prompt_omits_project_state(project_with_placeholder_library):
    """HATS-681: SubAgentRunner must NOT inject PROJECT_STATE (the STATE.md
    backlog dump) into the legacy meta-prompt. On-data verification showed the
    dump was ~5.4K tok of mostly-completed-task dead weight per sub-agent run,
    and the dominant consumer (session-reviewer) never used it; the backlog is
    reachable on-demand via the `ai-hats task` CLI."""
    from ai_hats.paths import state_md_path
    from ai_hats.providers import get_provider
    from ai_hats.runtime import SubAgentRunner

    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("ph-role", provider_name="claude")
    result = asm.composer.compose("ph-role")

    # STATE.md exists with real backlog content — the thing we must NOT inject.
    state_md_path(project).write_text(
        "# Task State\n\n## DONE\n- **HATS-001**: SENTINEL_DONE_TASK\n"
    )

    from ai_hats.observe import SessionManager

    runner = SubAgentRunner(
        project,
        _subagent_payload(result),
        session_mgr=SessionManager(project, runs_dir=runs_dir(project)),
    )
    meta_prompt = runner._build_meta_prompt(
        result=result,
        provider=get_provider("claude"),
        task="do the real thing",
        ticket_id="",
    )

    assert "# TASK" in meta_prompt  # the real task still lands
    assert "# PROJECT_STATE" not in meta_prompt
    assert "SENTINEL_DONE_TASK" not in meta_prompt


def test_subagent_sdk_first_message_omits_project_state(project_with_placeholder_library):
    """HATS-681: the SDK first-user-message (captured in the meta_prompt.txt
    forensic audit) must not carry PROJECT_STATE either — same rationale as the
    legacy path."""
    from ai_hats.paths import state_md_path
    from ai_hats.runtime import SubAgentRunner

    project, lib = project_with_placeholder_library
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("ph-role", provider_name="claude")
    result = asm.composer.compose("ph-role")

    state_md_path(project).write_text(
        "# Task State\n\n## DONE\n- **HATS-001**: SENTINEL_DONE_TASK\n"
    )

    from ai_hats.observe import SessionManager

    runner = SubAgentRunner(
        project,
        _subagent_payload(result),
        session_mgr=SessionManager(project, runs_dir=runs_dir(project)),
    )
    audit = runner._build_sdk_prompt_audit(
        result=result,
        task="do the real thing",
        ticket_id="",
    )

    assert "# TASK" in audit  # the real task still lands
    assert "# PROJECT_STATE" not in audit
    assert "SENTINEL_DONE_TASK" not in audit


# ---------- HATS-549 Phase 4: hook partition ----------


def test_v4_partition_routes_user_hook_to_user_hooks_namespace(project_with_library):
    """User-authored .py under .agent/hooks/ lands under <ai_hats_dir>/user-hooks/
    (NOT under the managed library/hooks/ namespace)."""
    from ai_hats.assembler import Assembler

    project_dir, _ = project_with_library
    # Seed v3 user-owned hook
    legacy_dir = project_dir / ".agent" / "hooks"
    legacy_dir.mkdir(parents=True)
    user_hook = legacy_dir / "my_secret_guard.py"
    user_hook.write_text("#!/usr/bin/env python3\n")
    user_hook.chmod(0o755)
    (legacy_dir / "secret_guard_rules.yaml").write_text("rules: []\n")

    asm = Assembler(project_dir)
    asm._migrate_layout_v4_hooks_partition()

    user_dst = project_dir / ".agent" / "ai-hats" / "user-hooks"
    assert (user_dst / "my_secret_guard.py").exists()
    assert (user_dst / "secret_guard_rules.yaml").exists()
    # Managed namespace untouched by foreign content.
    managed_dst = project_dir / ".agent" / "ai-hats" / "library" / "hooks"
    assert not (managed_dst / "my_secret_guard.py").exists()
    # Source removed (cleaned up after partition).
    assert not legacy_dir.exists()


def test_v4_partition_routes_managed_hook_to_library_hooks(project_with_library):
    """An ai-hats-owned hook basename routes to library/hooks/ (managed
    namespace) — preserves the historical placement."""
    from ai_hats.assembler import Assembler, _ai_hats_owned_hook_basenames

    project_dir, _ = project_with_library
    owned = _ai_hats_owned_hook_basenames()
    # If the whitelist is empty (broken package data in test env),
    # the test has nothing meaningful to assert.
    if not owned:
        return
    managed_basename = next(iter(owned))

    legacy_dir = project_dir / ".agent" / "hooks"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / managed_basename).write_text("#!/bin/sh\n")

    asm = Assembler(project_dir)
    asm._migrate_layout_v4_hooks_partition()

    assert (project_dir / ".agent" / "ai-hats" / "library" / "hooks" / managed_basename).exists()
    assert not (project_dir / ".agent" / "ai-hats" / "user-hooks" / managed_basename).exists()


def test_v4_partition_idempotent_on_already_migrated_state(project_with_library):
    """Re-running the partition on a partially-migrated project is a no-op."""
    from ai_hats.assembler import Assembler

    project_dir, _ = project_with_library
    asm = Assembler(project_dir)
    # First call: nothing to do.
    asm._migrate_layout_v4_hooks_partition()
    # Second call: still nothing to do, no exception.
    asm._migrate_layout_v4_hooks_partition()


def test_v4_partition_routes_subdir_to_user_hooks(project_with_library):
    """Subdirs under .agent/hooks/ (e.g. tests/) are user content and
    move to user-hooks/ in one shot."""
    from ai_hats.assembler import Assembler

    project_dir, _ = project_with_library
    legacy_dir = project_dir / ".agent" / "hooks" / "tests"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "smoke.sh").write_text("#!/bin/sh\nexit 0\n")

    asm = Assembler(project_dir)
    asm._migrate_layout_v4_hooks_partition()

    assert (project_dir / ".agent" / "ai-hats" / "user-hooks" / "tests" / "smoke.sh").exists()


def test_owned_basenames_includes_shared_state_guard():
    """Regression guard: the framework's primary PreToolUse hook
    basename must always be in the whitelist — otherwise the v4
    partition would route ai-hats's own hook into user-hooks/."""
    from ai_hats.assembler import _ai_hats_owned_hook_basenames

    owned = _ai_hats_owned_hook_basenames()
    # In dev test environments importlib.resources may not resolve to
    # package data; allow empty in that case, but if non-empty the
    # core hook must be there.
    if owned:
        assert "pre_bash_shared_state_guard.sh" in owned


def test_v4_partition_reconciles_stuck_state_in_managed_namespace(project_with_library):
    """HATS-549 review fix: a user-owned hook that landed in
    library/hooks/ via a pre-Phase-4 auto-heal must be moved out to
    user-hooks/ on the next bump — otherwise it stays in the managed
    namespace where future framework sweeps could discard it."""
    from ai_hats.assembler import Assembler

    project_dir, _ = project_with_library
    # Simulate stuck-state: foreign .py already sitting in managed
    # namespace (no legacy .agent/hooks/ to partition this time).
    managed = project_dir / ".agent" / "ai-hats" / "library" / "hooks"
    managed.mkdir(parents=True)
    (managed / "stuck.py").write_text("#!/usr/bin/env python3\n")
    # Also: a framework bookkeeping file that must survive.
    (managed / ".manifest").write_text("pre_bash_shared_state_guard.sh\n")

    asm = Assembler(project_dir)
    asm._migrate_layout_v4_hooks_partition()

    # User-owned file relocated to user-hooks/.
    assert (project_dir / ".agent" / "ai-hats" / "user-hooks" / "stuck.py").exists()
    assert not (managed / "stuck.py").exists()
    # Framework bookkeeping preserved.
    assert (managed / ".manifest").exists()


def test_v4_partition_reconcile_preserves_managed_hook(project_with_library):
    """Reconciliation pass MUST NOT touch ai-hats-owned hooks already
    in library/hooks/ — they belong there."""
    from ai_hats.assembler import Assembler, _ai_hats_owned_hook_basenames

    project_dir, _ = project_with_library
    owned = _ai_hats_owned_hook_basenames()
    if not owned:
        return
    managed_name = next(iter(owned))
    managed = project_dir / ".agent" / "ai-hats" / "library" / "hooks"
    managed.mkdir(parents=True)
    (managed / managed_name).write_text("#!/bin/sh\n")

    asm = Assembler(project_dir)
    asm._migrate_layout_v4_hooks_partition()

    # Managed file stays put.
    assert (managed / managed_name).exists()
    # user-hooks not created for this scenario.
    assert not (project_dir / ".agent" / "ai-hats" / "user-hooks" / managed_name).exists()


def test_split_user_hook_command_recognises_post_heal_form():
    """Phase 4 pre-pass must also detect post-heal
    .agent/ai-hats/library/hooks/<x> form so REPEAT-bumps on
    pre-HATS-549 stuck states heal cleanly (review fix A.4)."""
    from ai_hats.migration_healer import _split_user_hook_command

    assert (
        _split_user_hook_command("$CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/foreign.py")
        == "foreign.py"
    )
    assert _split_user_hook_command(".agent/ai-hats/library/hooks/foreign.py") == "foreign.py"
    # Legacy form still works.
    assert _split_user_hook_command(".agent/hooks/foreign.py") == "foreign.py"
    # Non-hook paths return None.
    assert _split_user_hook_command("echo hello") is None
    assert _split_user_hook_command(".agent/ai-hats/library/rules/x.md") is None


def test_unknown_provider_in_yaml_fails_loud_at_load(tmp_path):
    """AC-5 parity (HATS-863): the schema no longer validates ``provider``
    (schema→providers back-edge severed); a hand-edited ai-hats.yaml with an
    unknown provider must still fail loud at the assembler read chokepoint."""
    project = tmp_path / "project"
    project.mkdir()
    (project / PROJECT_CONFIG).write_text("provider: bogus-provider\n")

    with pytest.raises(ValueError, match="bogus-provider"):
        Assembler(project_dir=project)
