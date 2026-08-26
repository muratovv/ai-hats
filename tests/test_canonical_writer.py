"""Tests for the canonical writer after HATS-1203.

Post-HATS-1203 contract:
- ``write_canonical`` emits NO framework artefact — not even ``imports.md``,
  which lost its last reader when HATS-1170 dropped the root ``CLAUDE.md``
  scaffold. Only ``user-rules/`` and the (now empty) ``MANAGED`` manifest.
- An ``imports.md`` from an older layout is swept like any stale managed file,
  as are v0.6 framework files; ``user-rules/`` is never deleted.
- Delivery moved to the composed prompt — ``test_user_rules_composition.py``.
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
from ai_hats.constants import LAUNCHER_CONTRACT, LAUNCHER_CONTRACT_FILE
from ai_hats.paths import PROJECT_CONFIG


@pytest.fixture
def project_with_library(tmp_path: Path) -> Path:
    """Build a minimal project + library so Assembler can compose a real role."""
    project = tmp_path / "proj"
    project.mkdir()

    lib = project / "libraries"

    rule_a = lib / "rules" / "rule_a"
    rule_a.mkdir(parents=True)
    (rule_a / "rule.md").write_text("# rule_a\nDo A.\n")

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

    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    return project


def _compose(project: Path, role: str = "test-role"):
    asm = Assembler(project)
    return asm, asm.composer.compose(role)


def _canonical(project: Path) -> Path:
    return project / AGENT_DIR / CANONICAL_DIR


def _manifest_entries(project: Path) -> list[str]:
    manifest = (_canonical(project) / CANONICAL_MANIFEST).read_text()
    return [ln for ln in manifest.splitlines() if ln.strip() and not ln.startswith("#")]


# --------------------------------------------------------------------- #
# No framework artefact on disk — imports.md included
# --------------------------------------------------------------------- #


def test_write_canonical_emits_no_framework_files(project_with_library: Path) -> None:
    """HATS-1203: no imports.md, and no priorities/role/traits/rules/skills_index."""
    asm, _ = _compose(project_with_library)
    asm.write_canonical()

    canonical = _canonical(project_with_library)
    assert not (canonical / "imports.md").exists(), "aggregator was retired by HATS-1203"
    for stripped in ("priorities.md", "role.md", "skills_index.md"):
        assert not (canonical / stripped).exists()
    assert not (canonical / "traits").exists()
    assert not (canonical / "rules").exists()


def test_write_canonical_stamps_launcher_contract(project_with_library: Path) -> None:
    """HATS-1617: the bash launcher has no venv to ask on its failure path, so the
    contract it must match is left on disk for it to read."""
    asm, _ = _compose(project_with_library)
    asm.write_canonical()

    stamp = _canonical(project_with_library) / LAUNCHER_CONTRACT_FILE
    assert stamp.read_text().strip() == str(LAUNCHER_CONTRACT)


def test_write_canonical_creates_empty_user_rules_dir(project_with_library: Path) -> None:
    """``user-rules/`` is always created — it is the landing zone the composed
    prompt reads at session time."""
    asm, _ = _compose(project_with_library)
    asm.write_canonical()
    assert (_canonical(project_with_library) / USER_RULES_SUBDIR).is_dir()


def test_write_canonical_manifest_tracks_nothing(project_with_library: Path) -> None:
    """MANAGED manifest is empty — the canonical layer manages no file now."""
    asm, _ = _compose(project_with_library)
    asm.write_canonical()
    assert _manifest_entries(project_with_library) == []


def test_write_canonical_leaves_only_user_rules_and_manifest(project_with_library: Path) -> None:
    """The whole canonical root: ``user-rules/``, the MANAGED manifest, and the
    launcher-contract stamp (HATS-1617)."""
    asm, _ = _compose(project_with_library)
    asm.write_canonical()

    on_disk = {p.name for p in _canonical(project_with_library).iterdir()}
    assert on_disk == {USER_RULES_SUBDIR, CANONICAL_MANIFEST, LAUNCHER_CONTRACT_FILE}


def test_write_canonical_does_not_aggregate_user_rules(project_with_library: Path) -> None:
    """User-rules present → still no aggregator. They travel in the composed
    prompt, not through an on-disk ``@``-import list.
    """
    canonical = _canonical(project_with_library)
    user_rules = canonical / USER_RULES_SUBDIR
    user_rules.mkdir(parents=True, exist_ok=True)
    (user_rules / "a_first.md").write_text("a")
    (user_rules / "z_last.md").write_text("z")

    asm, _ = _compose(project_with_library)
    asm.write_canonical()

    assert not (canonical / "imports.md").exists()
    assert _manifest_entries(project_with_library) == []
    # The rules themselves are untouched data.
    assert (user_rules / "a_first.md").read_text() == "a"
    assert (user_rules / "z_last.md").read_text() == "z"


# --------------------------------------------------------------------- #
# Idempotency + stale cleanup
# --------------------------------------------------------------------- #


def test_write_canonical_idempotent_no_op(project_with_library: Path) -> None:
    """Re-running is a no-op for the manifest mtime."""
    asm, _ = _compose(project_with_library)
    asm.write_canonical()
    manifest = _canonical(project_with_library) / CANONICAL_MANIFEST
    first_mtime = manifest.stat().st_mtime_ns

    asm.write_canonical()
    assert manifest.stat().st_mtime_ns == first_mtime


def test_write_canonical_sweeps_pre_existing_imports_md(project_with_library: Path) -> None:
    """HATS-1203 retirement heal: an aggregator from the previous layout, still
    claimed by the manifest, is removed on the next refresh.
    """
    canonical = _canonical(project_with_library)
    canonical.mkdir(parents=True, exist_ok=True)
    (canonical / "imports.md").write_text("@./user-rules/mine.md\n")
    (canonical / CANONICAL_MANIFEST).write_text("imports.md\n")

    asm, _ = _compose(project_with_library)
    asm.write_canonical()

    assert not (canonical / "imports.md").exists(), "stale aggregator was not swept"
    assert _manifest_entries(project_with_library) == []


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

    asm, _ = _compose(project_with_library)
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
    (canonical / CANONICAL_MANIFEST).write_text(f"{USER_RULES_SUBDIR}/my-rule.md\nimports.md\n")

    asm, _ = _compose(project_with_library)
    asm.write_canonical()

    assert (user_rules / "my-rule.md").read_text() == "# user content"


# --------------------------------------------------------------------- #
# Composition input is decoupled from the writer
# --------------------------------------------------------------------- #


def test_write_canonical_ignores_priorities_role_traits_rules_skills(
    project_with_library: Path,
) -> None:
    """Non-empty ``result.priorities`` / ``role_injection`` / etc. produce no
    on-disk files. Regression test for the HATS-294 contract: composition is
    consumed per-session by Provider, never materialized.
    """
    asm, result = _compose(project_with_library)
    assert result.priorities  # sanity: the fixture role declares priorities
    assert result.role_injection
    assert result.trait_injections
    assert result.rules
    assert result.skills

    asm.write_canonical()

    # The launcher stamp is install metadata, not composition output — it is the
    # only non-composition file this root gained (HATS-1617).
    on_disk = {p.name for p in _canonical(project_with_library).iterdir()}
    assert on_disk == {USER_RULES_SUBDIR, CANONICAL_MANIFEST, LAUNCHER_CONTRACT_FILE}


def test_set_role_writes_canonical(project_with_library: Path) -> None:
    """``set_role`` triggers ``write_canonical`` — sanity check the wiring."""
    asm, _ = _compose(project_with_library)
    asm.init()
    asm.set_role("test-role", provider_name="claude")

    canonical = _canonical(project_with_library)
    assert (canonical / USER_RULES_SUBDIR).is_dir()
    assert (canonical / CANONICAL_MANIFEST).is_file()
