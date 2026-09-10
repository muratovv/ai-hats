"""HATS-1268 — the shared-state guard reaches every role, still.

It used to be wired unconditionally from a hardcoded provider entry, so role
composition could not affect it. Declaring it on ``safety-guard`` made the
wiring composition-gated, which is a silent coverage cut unless every role
composes a trait that carries the skill — the HATS-514 / HYP-014 class.

Measured over all 19 roles at the move: 19 before, 19 after. Attachment points
are ``trait-base`` (16) and ``trait-analyst-base`` (judge-auditor, role-auditor,
role-judge — they compose neither trait-base nor trait-agent).

Fail-under-revert: drop ``safety-guard`` from either trait and the roles behind
it show up in the assertion message.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.hook_collection import composed_rows
from ai_hats.materialization import PlanMaterializer
from ai_hats.paths import claude_plugin_skills_dir

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
GUARD_BASENAME = "pre_bash_shared_state_guard.sh"


def _builtin_roles() -> list[str]:
    return sorted(p.parent.name for p in LIBRARY.glob("*/roles/*/config.yaml"))


def _guard_wired(role: str) -> bool:
    result = Assembler(REPO_ROOT).composer.compose(role)
    skills_dir = claude_plugin_skills_dir(Path("/probe/plugin"))
    port = PlanMaterializer()  # the mirror as the SKILLS handler would record it
    for skill in result.skills:
        port.copy_tree(skill.source_path, skills_dir / skill.name)
    rows, _notices = composed_rows(result, skills_dir, port=port)
    return any(
        GUARD_BASENAME in row["command"] for event_rows in rows.values() for row in event_rows
    )


def test_the_role_catalog_is_not_empty() -> None:
    """Positive control: an empty catalog makes the next test vacuously true."""
    roles = _builtin_roles()
    assert len(roles) >= 15, f"role catalog looks wrong: {roles}"


def test_every_builtin_role_still_gets_the_shared_state_guard() -> None:
    uncovered = [role for role in _builtin_roles() if not _guard_wired(role)]
    assert not uncovered, (
        f"roles that lost the shared-state guard: {uncovered}. "
        "Wiring is composition-gated since HATS-1268 — a role reaches the guard "
        "only through a trait carrying the safety-guard skill."
    )
