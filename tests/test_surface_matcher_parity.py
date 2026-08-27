"""Every shipped matcher, against the tool names surfaces actually send.

The suite this joins had a blind spot with a name: every dispatcher test fed its
surface ``tool_name: "Bash"`` — a Claude-vocabulary name no surface sends — so a
surface whose name table was empty passed every test while running none of its
gates. This drives each surface's LIVE matching path with observed names. The
entries stay pointed at that live path: as a surface moves onto its profile the
path becomes profile-backed and this file does not change, which is what makes
a green run evidence about the surface rather than about the new table.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.surfaces import profiles
from ai_hats.surfaces.agy import claude_hook_adapter as agy_adapter
from ai_hats.surfaces.cline import claude_hook_adapter as cline_adapter
from ai_hats.surfaces.codex import claude_hook_adapter as codex_adapter

_LIBRARY = Path(__file__).resolve().parents[1] / "packages/ai-hats-library/src/ai_hats_library"


def _shipped_matchers() -> dict[str, list[str]]:
    """``{skill: [matcher]}`` read from the shipped skills, not restated here.

    Restating them would let a skill change its matcher while this test kept
    asserting the old one — green, and measuring nothing.
    """
    import yaml

    found: dict[str, list[str]] = {}
    for skill_md in sorted(_LIBRARY.glob("*/skills/*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        front = yaml.safe_load(text.split("---", 2)[1]) or {}
        hooks = ((front.get("ai_hats") or {}).get("runtime_hooks")) or {}
        rows = [row for entries in hooks.values() for row in entries or []]
        matchers = [str(r["matcher"]) for r in rows if isinstance(r, dict) and r.get("matcher")]
        if matchers:
            found[skill_md.parent.name] = matchers
    return found


def _cline_tool(native: str) -> str:
    """The name cline's own adapter hands the matcher for ``native``."""
    payloads = cline_adapter.to_claude_hook_payloads(
        {"preToolUse": {"toolName": native, "parameters": {"command": "true"}}},
        "PreToolUse",
    )
    return str(payloads[0]["tool_name"]) if payloads else ""


#: The live matching path per surface: native tool name in, verdict out.
#: agy is the one still reading a table of its own; codex and cline read profiles.
LIVE = {
    "agy": lambda m, native: agy_adapter.matches_claude_hook(
        m, agy_adapter.agy_tool_name({"tool_name": native})
    ),
    "cline": lambda m, native: cline_adapter.matches_claude_hook(m, _cline_tool(native)),
    "codex": lambda m, native: codex_adapter.matches_claude_hook(m, native),
}

#: What each surface calls the tool a `Bash` matcher is written to guard.
#: codex's is from its own rollout logs; the others from their name tables.
TERMINAL = {
    "agy": ("run_command", "execute"),
    "cline": ("bash", "execute_command", "run_commands"),
    "codex": ("exec", "shell", "local_shell"),
}

#: What each surface calls the tools an `Edit|Write|MultiEdit` matcher guards.
FILE_MUTATION = {
    "agy": ("Create", "write_to_file", "replace_file_content", "multi_replace_file_content"),
    "cline": ("write_to_file", "replace_in_file", "editor", "apply_patch"),
    "codex": ("apply_patch",),
}

_TERMINAL_MATCHERS = ("Bash", "Bash|run_command|execute")
_MUTATION_MATCHER = "Edit|Write|MultiEdit"


@pytest.mark.parametrize("surface", sorted(LIVE))
@pytest.mark.parametrize("matcher", _TERMINAL_MATCHERS)
def test_a_terminal_matcher_reaches_the_terminal_tool(surface: str, matcher: str) -> None:
    """A `Bash` gate must fire on whatever that surface calls its shell."""
    live = LIVE[surface]
    for native in TERMINAL[surface]:
        assert live(matcher, native), (
            f"{surface}: matcher {matcher!r} does not reach {native!r} — "
            f"every gate written against it is silently off on this surface"
        )


@pytest.mark.parametrize("surface", sorted(LIVE))
def test_a_mutation_matcher_reaches_the_file_tools(surface: str) -> None:
    live = LIVE[surface]
    for native in FILE_MUTATION[surface]:
        assert live(_MUTATION_MATCHER, native), (
            f"{surface}: matcher {_MUTATION_MATCHER!r} does not reach {native!r}"
        )


@pytest.mark.parametrize("surface", sorted(LIVE))
@pytest.mark.parametrize("matcher", (*_TERMINAL_MATCHERS, _MUTATION_MATCHER))
def test_positive_control_a_matcher_matches_its_own_literal_name(
    surface: str, matcher: str
) -> None:
    """The control for the two above: a Claude name matches on every surface.

    Without it a green run above is indistinguishable from a matcher that
    accepts everything, and a red one from a broken harness.
    """
    literal = matcher.split("|")[0]
    assert LIVE[surface](matcher, literal), f"{surface}: harness broken — {literal!r} must match"


@pytest.mark.parametrize("surface", sorted(LIVE))
def test_negative_control_an_unrelated_tool_does_not_match(surface: str) -> None:
    """The other control: these matchers are not simply matching everything."""
    assert not LIVE[surface]("Bash", "read_file_contents_somehow")


def test_every_shipped_matcher_is_covered_by_this_test() -> None:
    """A new shipped matcher must not join the library unnoticed by this file."""
    covered = {*_TERMINAL_MATCHERS, _MUTATION_MATCHER, "EnterWorktree"}
    shipped = {m for matchers in _shipped_matchers().values() for m in matchers}
    assert shipped, "no shipped matchers found — the reader, not the library, is broken"
    assert shipped <= covered, (
        f"shipped matchers this test does not exercise: {sorted(shipped - covered)}"
    )


@pytest.mark.parametrize("surface", sorted(LIVE))
def test_the_profile_agrees_with_the_live_path(surface: str) -> None:
    """The catalog row and the running code must not drift apart.

    This is what lets each surface's ``LIVE`` entry be replaced by the profile
    without changing what the tests above measure.
    """
    profile = {"agy": profiles.AGY, "cline": profiles.CLINE, "codex": profiles.CODEX}[surface]
    from ai_hats.surfaces.hook_channel import matches

    for matcher in (*_TERMINAL_MATCHERS, _MUTATION_MATCHER):
        for native in (*TERMINAL[surface], *FILE_MUTATION[surface]):
            assert matches(profile, matcher, native) == LIVE[surface](matcher, native), (
                f"{surface}: profile and live path disagree on {matcher!r} x {native!r}"
            )
