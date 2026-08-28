"""Where an env-var name is allowed to be spelled (HATS-1868, folded HATS-1870).

``src/ai_hats/env.py`` called itself the single source of truth for reading
**all** ``os.environ`` variables while 24 literals sat in eleven other modules.
An unachievable claim is worse than none: a HATS-1858 plan item asking where to
register ``AI_HATS_HOOK_TIMEOUT_S`` was closed as having no answer, with the
home right there.

So the claim is narrowed to one this can hold: ``env.py`` is the home, and what
is spelled elsewhere is the closed list below. The list is today's inventory,
not an endorsement of it — what this gate holds is GROWTH: a module that is not
on it turns this red, so adding to the sprawl became an edit under review rather
than a literal nobody sees.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "ai_hats"

#: An env-var name as this package spells one. Deliberately wider than
#: ``AI_HATS_*``: the borrowed names (``XDG_*``) are names too, and a rule that
#: could not see them would miss the ones most easily duplicated.
ENV_NAME = re.compile(r"^(AI_HATS|AGY|CODEX|CLAUDE|RACK|XDG)_[A-Z0-9_]+$")

#: Modules that hold a name of their own, and why the name stays there. Each is
#: a deliberate exception to "env.py is the home" — not a backlog.
ELSEWHERE = {
    "constants.py": "install-time, PTY and verbosity knobs, a second home by HATS-1868 decision",
    "consent_wrapper.py": "the consent channel's own protocol, ADR-0029/0030",
    "worktree_hooks.py": "the wt channel's own budget",
    "session_identity.py": "the identity envelope names itself, ADR-0025 D1",
    "self_location.py": "a guard's own escape hatch",
    "retired_dists.py": "a one-shot prune's escape hatch",
    "migration_backup.py": "a bump's backup dir override",
    "update_check/__init__.py": "the update check's opt-out",
    "surfaces/codex/provider.py": "that surface's own XDG home override",
    "surfaces/opencode/provider.py": "that surface's own XDG home override",
    "surfaces/agy/hook_dispatcher.py": "runs on every tool call and must not import ai_hats",
    "pty_shutdown.py": "the PTY teardown's own two bounds",
    "pipeline/harness.py": "the pipeline's own trace knobs",
    "startup_notices.py": "the startup notice's own two switches",
    "wt_lifecycle.py": "the branch name handed to a wt hook",
    "cli/maintenance.py": "a test-only pause switch",
}


def _literal_names(path: Path) -> set[str]:
    """Every env-var name this module spells as a string literal.

    Read as syntax, not as text: a name inside a docstring or an error message
    is a mention, and only an assignment makes the module a HOME.
    """
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if _is_export_list(node):
            # `__all__` holds PYTHON names, and this package spells plenty of
            # them in the same shape (`AI_HATS_PROJECT_DIR_ENV`). Counting one
            # as a home would put four modules on the list for re-exporting.
            continue
        for value in ast.walk(node.value) if node.value is not None else ():
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                if ENV_NAME.match(value.value):
                    found.add(value.value)
    return found


def _is_export_list(node: ast.Assign | ast.AnnAssign) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets)


def _homes() -> dict[str, set[str]]:
    return {
        str(path.relative_to(SRC)): names
        for path in sorted(SRC.rglob("*.py"))
        if "/tests/" not in f"/{path.relative_to(SRC)}"
        for names in [_literal_names(path)]
        if names
    }


def test_no_module_outside_the_listed_ones_spells_an_env_name() -> None:
    stray = sorted(set(_homes()) - {"env.py"} - set(ELSEWHERE))
    assert stray == [], (
        f"these modules spell an env-var name with no entry in ELSEWHERE: {stray}. "
        f"Its home is src/ai_hats/env.py — import it from there, or add the module "
        f"to ELSEWHERE with the reason it belongs outside."
    )


def test_the_positive_control_a_stray_literal_is_actually_caught(tmp_path: Path) -> None:
    """Without this, a green run above is indistinguishable from a walk that
    parsed nothing — which is how the claim it replaces came to be false."""
    stray = tmp_path / "impostor.py"
    stray.write_text('SOMETHING = "AI_HATS_NOT_A_REAL_NAME"\n', encoding="utf-8")
    assert _literal_names(stray) == {"AI_HATS_NOT_A_REAL_NAME"}


def test_a_name_only_mentioned_in_prose_is_not_a_home(tmp_path: Path) -> None:
    """The negative control: every refusal message in this package names the
    hatch it opens, and a text scan would have called each one a second home."""
    mention = tmp_path / "prose.py"
    mention.write_text(
        '"""Set AI_HATS_GATE_BROKEN_ACK=1 to proceed."""\n'
        "def say():\n"
        '    raise RuntimeError("set AI_HATS_GATE_BROKEN_ACK=1")\n',
        encoding="utf-8",
    )
    assert _literal_names(mention) == set()


def test_every_listed_exception_still_holds_a_name() -> None:
    """A stale entry re-opens the hole quietly: it permits a module that no
    longer needs permission, and the next literal to land there is invisible."""
    homes = _homes()
    dead = sorted(name for name in ELSEWHERE if name not in homes)
    assert dead == [], f"ELSEWHERE names modules that spell nothing any more: {dead}"


def test_the_withheld_roster_and_the_home_cannot_drift_apart() -> None:
    """One name both leaves need is spelled twice BY CONSTRUCTION.

    ``constants`` and ``env`` are both import leaves (``test_import_hygiene``),
    so neither may import the other. The pair that matters is the two delivery
    hatches: they are in ``env`` because the refusals name them, and in
    ``constants`` because the launcher withholds them from sub-agents. Rename
    one and the roster goes on withholding a flag nobody sets, in silence.
    """
    from ai_hats.constants import BYPASS_FLAGS_NOT_INHERITED
    from ai_hats.env import ENV_GATE_BROKEN_ACK, ENV_GIT_GATE_BROKEN_ACK

    withheld = {ENV_GATE_BROKEN_ACK, ENV_GIT_GATE_BROKEN_ACK}
    assert withheld <= BYPASS_FLAGS_NOT_INHERITED, (
        f"the roster no longer withholds {sorted(withheld - BYPASS_FLAGS_NOT_INHERITED)} — "
        f"a hatch a sub-agent can set is a hatch that is not a person's"
    )


@pytest.mark.parametrize(
    "name",
    [
        "AI_HATS_HOOK_TIMEOUT_S",
        "AI_HATS_GATE_BROKEN_ACK",
        "AI_HATS_GIT_HOOK_TIMEOUT_S",
        "AI_HATS_GIT_GATE_BROKEN_ACK",
        "AI_HATS_HOOK_SURFACE_TIMEOUT_MS",
        "AI_HATS_AGY_HOOK_TIMEOUT_S",
        "AI_HATS_HOOK_EVENT",
    ],
)
def test_the_two_gate_channels_names_came_home(name: str) -> None:
    """The names the two gate channels used to spell for themselves."""
    assert name in _literal_names(SRC / "env.py")
