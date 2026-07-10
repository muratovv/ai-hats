"""SKILL.md purity guard (HATS-876 / T18, ADR-0014 §4; supervisor decision 1).

Every shipped skill stays valid drop-in Agent-Skill format: the pure keys are
present and ai-hats composition metadata lives ONLY under the namespaced
``ai_hats:`` frontmatter key (which a non-ai-hats agent ignores) — never as bare
top-level keys. Guards the HATS-814 model against regressions that would break
the drop-in-clone visibility win.
"""

from __future__ import annotations

import pytest

from ai_hats.frontmatter import read_frontmatter
from ai_hats.paths import builtin_library_layers

# Composition keys the HATS-814 cutover nests under ``ai_hats:``. A truthy one at
# the TOP level of frontmatter is the leak this guard catches.
_COMPOSITION_KEYS = ("git_hooks", "runtime_hooks", "worktree")


def _all_skill_md():
    for layer in builtin_library_layers():
        skills = layer / "skills"
        if not skills.is_dir():
            continue
        for skill_dir in sorted(skills.iterdir()):
            md = skill_dir / "SKILL.md"
            if md.is_file():
                yield md


@pytest.fixture(scope="module")
def skill_mds():
    mds = list(_all_skill_md())
    assert mds, "no shipped SKILL.md found — is the library resolving?"
    return mds


def test_every_skill_has_pure_agent_skill_keys(skill_mds):
    missing = []
    for md in skill_mds:
        fm = read_frontmatter(md) or {}
        if not (fm.get("name") and fm.get("description")):
            missing.append(md.parent.name)
    assert not missing, f"SKILL.md missing pure name/description: {missing}"


def test_no_composition_metadata_leaks_to_top_level(skill_mds):
    leaks = {}
    for md in skill_mds:
        fm = read_frontmatter(md) or {}
        bad = [k for k in _COMPOSITION_KEYS if fm.get(k)]
        if bad:
            leaks[md.parent.name] = bad
    assert not leaks, (
        "composition metadata must live under the namespaced 'ai_hats:' key, not "
        f"at the top level of SKILL.md frontmatter: {leaks}"
    )
