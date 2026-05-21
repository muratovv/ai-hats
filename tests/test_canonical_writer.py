"""Tests for the canonical writer after HATS-294.

Post-HATS-294 contract:
- ``write_canonical`` emits ONLY ``imports.md`` (a list of user-rules
  ``@-import`` lines) plus the ``MANAGED`` manifest tracking it.
- Framework content (priorities / role / traits / rules / skills_index)
  is composed in memory per-session by ``Provider.build_session_prompt``
  and never materialized on disk.
- ``user-rules/`` is never deleted by stale cleanup.
- Stale framework files from prior v0.6 layouts are swept on first call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.assembler import (
    AGENT_DIR,
    CANONICAL_DIR,
    CANONICAL_MANIFEST,
    USER_RULES_SUBDIR,
    Assembler,
)


@pytest.fixture
def project_with_library(tmp_path: Path) -> Path:
    """Build a minimal project + library so Assembler can compose a real role."""
    project = tmp_path / "proj"
    project.mkdir()

    lib = project / "libraries"

    rule_a = lib / "rules" / "rule_a"
    rule_a.mkdir(parents=True)
    (rule_a / "rule.md").write_text("# rule_a\nDo A.\n")
    (rule_a / "metadata.yaml").write_text("name: rule_a\n")

    skill_x = lib / "skills" / "skill_x"
    skill_x.mkdir(parents=True)
    (skill_x / "SKILL.md").write_text(
        "---\nname: skill_x\ndescription: Does X efficiently\n---\n# Skill X\n"
    )

    trait_alpha = lib / "traits" / "trait-alpha"
    trait_alpha.mkdir(parents=True)
    (trait_alpha / "config.yaml").write_text(
        "name: trait-alpha\n"
        "composition:\n  rules: [rule_a]\n"
        "injection: |\n  Alpha trait injection.\n"
    )

    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "priorities: [Reliability, Velocity]\n"
        "composition:\n  traits: [trait-alpha]\n  skills: [skill_x]\n"
        "injection: |\n  Role injection text.\n"
    )

    (project / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    return project


def _compose(project: Path, role: str = "test-role"):
    asm = Assembler(project)
    return asm, asm.composer.compose(role)


def _canonical(project: Path) -> Path:
    return project / AGENT_DIR / CANONICAL_DIR


# --------------------------------------------------------------------- #
# imports.md is the only framework artefact on disk
# --------------------------------------------------------------------- #


def test_write_canonical_emits_imports_md_only(project_with_library: Path) -> None:
    """No priorities.md / role.md / traits/ / rules/ / skills_index.md on disk."""
    asm, result = _compose(project_with_library)
    asm.write_canonical()

    canonical = _canonical(project_with_library)
    assert (canonical / "imports.md").is_file()

    # Nothing else managed by ai-hats lives at the canonical root.
    for stripped in ("priorities.md", "role.md", "skills_index.md"):
        assert not (canonical / stripped).exists()
    assert not (canonical / "traits").exists()
    assert not (canonical / "rules").exists()


def test_write_canonical_creates_empty_user_rules_dir(project_with_library: Path) -> None:
    """``user-rules/`` is always created so the aggregator has a place to look."""
    asm, result = _compose(project_with_library)
    asm.write_canonical()
    assert (_canonical(project_with_library) / USER_RULES_SUBDIR).is_dir()


def test_write_canonical_manifest_tracks_imports_only(project_with_library: Path) -> None:
    """MANAGED manifest tracks ``imports.md`` and nothing else."""
    asm, result = _compose(project_with_library)
    asm.write_canonical()

    manifest = (_canonical(project_with_library) / CANONICAL_MANIFEST).read_text()
    entries = [
        ln for ln in manifest.splitlines() if ln.strip() and not ln.startswith("#")
    ]
    assert entries == ["imports.md"]


# --------------------------------------------------------------------- #
# imports.md content: user-rules only
# --------------------------------------------------------------------- #


def test_imports_aggregator_empty_when_no_user_rules(project_with_library: Path) -> None:
    """No user-rules → aggregator is an empty file (still tracked in manifest)."""
    asm, result = _compose(project_with_library)
    asm.write_canonical()
    assert (_canonical(project_with_library) / "imports.md").read_text() == ""


def test_imports_aggregator_lists_user_rules_sorted(project_with_library: Path) -> None:
    """Aggregator emits ``@./user-rules/*.md`` in sorted order, one per line."""
    canonical = _canonical(project_with_library)
    canonical.mkdir(parents=True, exist_ok=True)
    user_rules = canonical / USER_RULES_SUBDIR
    user_rules.mkdir(exist_ok=True)
    (user_rules / "z_last.md").write_text("z")
    (user_rules / "a_first.md").write_text("a")
    (user_rules / "m_middle.md").write_text("m")

    asm, result = _compose(project_with_library)
    asm.write_canonical()

    body = (canonical / "imports.md").read_text()
    assert body == (
        "@./user-rules/a_first.md\n"
        "@./user-rules/m_middle.md\n"
        "@./user-rules/z_last.md\n"
    )


def test_imports_aggregator_no_framework_refs(project_with_library: Path) -> None:
    """No legacy ``@./priorities.md``, ``@./role.md``, ``@./traits/...``, etc."""
    asm, result = _compose(project_with_library)
    asm.write_canonical()

    body = (_canonical(project_with_library) / "imports.md").read_text()
    for forbidden in ("priorities.md", "role.md", "skills_index.md", "traits/", "rules/"):
        assert forbidden not in body, f"aggregator must not reference {forbidden!r}"


# --------------------------------------------------------------------- #
# Idempotency + stale cleanup
# --------------------------------------------------------------------- #


def test_write_canonical_idempotent_no_op(project_with_library: Path) -> None:
    """Re-running with the same result is a no-op for imports.md mtime."""
    asm, result = _compose(project_with_library)
    asm.write_canonical()
    imports_md = _canonical(project_with_library) / "imports.md"
    first_mtime = imports_md.stat().st_mtime_ns

    asm.write_canonical()
    assert imports_md.stat().st_mtime_ns == first_mtime


def test_write_canonical_sweeps_legacy_framework_files(project_with_library: Path) -> None:
    """v0.6 layout files (priorities/role/traits/rules/skills_index) recorded
    in the manifest are removed on first ``write_canonical`` call.
    """
    canonical = _canonical(project_with_library)
    canonical.mkdir(parents=True, exist_ok=True)
    # Simulate a v0.6 layout: planted files + manifest claiming them.
    (canonical / "priorities.md").write_text("# stale priorities")
    (canonical / "role.md").write_text("# stale role")
    (canonical / "skills_index.md").write_text("# stale index")
    (canonical / "traits").mkdir()
    (canonical / "traits" / "trait-alpha.md").write_text("# stale trait")
    (canonical / "rules").mkdir()
    (canonical / "rules" / "rule_a.md").write_text("# stale rule")
    (canonical / CANONICAL_MANIFEST).write_text(
        "priorities.md\nrole.md\nskills_index.md\ntraits/trait-alpha.md\n"
        "rules/rule_a.md\nimports.md\n"
    )

    asm, result = _compose(project_with_library)
    asm.write_canonical()

    assert not (canonical / "priorities.md").exists()
    assert not (canonical / "role.md").exists()
    assert not (canonical / "skills_index.md").exists()
    assert not (canonical / "traits").exists()
    assert not (canonical / "rules").exists()


def test_write_canonical_does_not_delete_user_rules(project_with_library: Path) -> None:
    """User-rules files survive stale cleanup even if the manifest lists them."""
    canonical = _canonical(project_with_library)
    canonical.mkdir(parents=True, exist_ok=True)
    user_rules = canonical / USER_RULES_SUBDIR
    user_rules.mkdir(exist_ok=True)
    (user_rules / "my-rule.md").write_text("# user content")
    # Manifest erroneously includes user-rules path (defense-in-depth check).
    (canonical / CANONICAL_MANIFEST).write_text(
        f"{USER_RULES_SUBDIR}/my-rule.md\nimports.md\n"
    )

    asm, result = _compose(project_with_library)
    asm.write_canonical()

    assert (user_rules / "my-rule.md").read_text() == "# user content"


# --------------------------------------------------------------------- #
# Composition input is decoupled from the writer
# --------------------------------------------------------------------- #


def test_write_canonical_ignores_priorities_role_traits_rules_skills(
    project_with_library: Path,
) -> None:
    """Non-empty ``result.priorities`` / ``role_injection`` / etc. produce no
    on-disk files. Regression test for HATS-294 contract: composition is
    consumed per-session by Provider, never materialized.
    """
    asm, result = _compose(project_with_library)
    assert result.priorities  # sanity: the fixture role declares priorities
    assert result.role_injection
    assert result.trait_injections
    assert result.rules
    assert result.skills

    asm.write_canonical()
    canonical = _canonical(project_with_library)

    # Only imports.md + user-rules/ subdir + MANAGED manifest on disk.
    on_disk = {p.name for p in canonical.iterdir()}
    assert on_disk == {"imports.md", USER_RULES_SUBDIR, CANONICAL_MANIFEST}


def test_set_role_writes_canonical(project_with_library: Path) -> None:
    """``set_role`` triggers ``write_canonical`` — sanity check the wiring."""
    asm, _ = _compose(project_with_library)
    asm.init()
    asm.set_role("test-role", provider_name="claude")

    assert (_canonical(project_with_library) / "imports.md").is_file()
