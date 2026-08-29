"""Every shipped matcher, against the tool names surfaces actually send.

The suite this joins had a blind spot with a name: every dispatcher test fed its
surface ``tool_name: "Bash"`` — a Claude-vocabulary name no surface sends — so a
surface whose name table was empty passed every test while running none of its
gates. This drives each surface's LIVE matching path with observed names, and
checks its own coverage against ``profiles.ALL`` — a surface left out is how the
first version missed opencode, one of the two whose defect it was written for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.surfaces import profiles
from ai_hats.surfaces.agy import claude_hook_adapter as agy_adapter
from ai_hats.surfaces.cline import claude_hook_adapter as cline_adapter
from ai_hats.surfaces.codex import claude_hook_adapter as codex_adapter
from ai_hats.surfaces.hook_channel import HookCall, matches

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


def _claude_call(native: str) -> HookCall:
    """No adapter: the harness already speaks the vocabulary hooks are written
    in, which is why claude's own table is empty."""
    return HookCall({"tool_name": native, "tool_input": {"command": "true"}}, native)


def _agy_call(native: str) -> HookCall:
    payload = {"toolCall": {"name": native, "args": {}}}
    return HookCall(agy_adapter.to_claude_payload(payload, "PreToolUse"), native)


def _cline_call(native: str) -> HookCall:
    payload = {"preToolUse": {"toolName": native, "parameters": {"command": "true"}}}
    calls = cline_adapter.to_claude_hook_calls(payload, "PreToolUse")
    return calls[0] if calls else HookCall({}, native)


def _codex_call(native: str) -> HookCall:
    payload = {"tool_name": native, "tool_input": {"command": "true"}, "cwd": "/tmp"}
    calls = codex_adapter.to_claude_hook_calls(payload, "PreToolUse")
    return calls[0] if calls else HookCall({}, native)


def _opencode_call(native: str) -> HookCall:
    from ai_hats.surfaces.opencode.hook_dispatcher import _spoken

    payload = {"tool_name": native, "tool_input": {}}
    return HookCall(_spoken(payload), native)


#: The live path per surface: native tool name in, the call the chain will match
#: on out. Each goes through the surface's OWN adapter, so a green run is
#: evidence about the surface rather than about the profile table.
LIVE = {
    "agy": _agy_call,
    "claude": _claude_call,
    "cline": _cline_call,
    "codex": _codex_call,
    "opencode": _opencode_call,
}

#: What each surface calls the tool a `Bash` matcher is written to guard.
#: codex's is from its own rollout logs; opencode's from its installed binary.
TERMINAL = {
    "agy": ("run_command", "execute"),
    "claude": ("Bash",),
    "cline": ("bash", "execute_command", "run_commands"),
    "codex": ("exec", "shell", "local_shell"),
    "opencode": ("bash",),
}

#: What each surface calls the tools an `Edit|Write|MultiEdit` matcher guards.
FILE_MUTATION = {
    "agy": ("Create", "write_to_file", "replace_file_content", "multi_replace_file_content"),
    "claude": ("Edit", "Write", "MultiEdit"),
    "cline": ("write_to_file", "replace_in_file", "editor", "apply_patch"),
    "codex": ("apply_patch",),
    "opencode": ("edit", "write", "patch"),
}

_TERMINAL_MATCHERS = ("Bash", "Bash|run_command|execute")
_MUTATION_MATCHER = "Edit|Write|MultiEdit"


def _fires(surface: str, matcher: str, native: str) -> bool:
    call = LIVE[surface](native)
    return matches(getattr(profiles, surface.upper()), matcher, call.tool)


def test_every_in_process_surface_is_exercised_here() -> None:
    """The hole this file was written to close, turned on the file itself.

    A surface missing from the tables below passes every test in it by not being
    named — which is how opencode, one of the two surfaces whose gates were
    measurably off, sat outside the test that found the other one.
    """
    covered = set(LIVE) & set(TERMINAL) & set(FILE_MUTATION)
    assert {p.label for p in profiles.ALL} <= covered, (
        f"surfaces with no parity coverage: {sorted({p.label for p in profiles.ALL} - covered)}"
    )


@pytest.mark.parametrize("surface", sorted(LIVE))
@pytest.mark.parametrize("matcher", _TERMINAL_MATCHERS)
def test_a_terminal_matcher_reaches_the_terminal_tool(surface: str, matcher: str) -> None:
    """A `Bash` gate must fire on whatever that surface calls its shell."""
    for native in TERMINAL[surface]:
        assert _fires(surface, matcher, native), (
            f"{surface}: matcher {matcher!r} does not reach {native!r} — "
            f"every gate written against it is silently off on this surface"
        )


@pytest.mark.parametrize("surface", sorted(LIVE))
def test_a_mutation_matcher_reaches_the_file_tools(surface: str) -> None:
    for native in FILE_MUTATION[surface]:
        assert _fires(surface, _MUTATION_MATCHER, native), (
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
    assert _fires(surface, matcher, literal), f"{surface}: harness broken — {literal!r} must match"


@pytest.mark.parametrize("surface", sorted(LIVE))
def test_negative_control_an_unrelated_tool_does_not_match(surface: str) -> None:
    """The other control: these matchers are not simply matching everything."""
    assert not _fires(surface, "Bash", "read_file_contents_somehow")


class TestAnEmptyToolTableIsRightByCheck:
    """claude's ``tool_names`` is empty, and the tests above cannot see it.

    Every one of them iterates a surface's own row, so on an empty row they
    iterate nothing and pass by not looking — which is exactly how opencode's
    missing argument row survived until HATS-1858. These drive the two readers
    directly instead.
    """

    def test_the_row_is_empty_on_purpose(self) -> None:
        """Not an oversight to be filled in later: the matcher vocabulary IS
        claude's vocabulary, so a table here would map names onto themselves."""
        assert profiles.CLAUDE.tool_names == {}
        assert profiles.CLAUDE.arg_names == {}

    def test_the_matcher_reader_falls_back_to_the_native_name(self) -> None:
        assert profiles.CLAUDE.matcher_names("Bash") == ("Bash",)
        assert profiles.CLAUDE.matcher_names("EnterWorktree") == ("EnterWorktree",)

    def test_the_payload_reader_falls_back_to_the_same_name(self) -> None:
        """The pair that must agree: a payload spelling a name the matcher would
        reject hands the hook a call its own row said it wanted."""
        for native in ("Bash", "Edit", "EnterWorktree"):
            assert profiles.CLAUDE.spoken_name(native) in profiles.CLAUDE.matcher_names(native)

    def test_the_claude_only_matcher_still_reaches_its_tool(self) -> None:
        """`EnterWorktree` is deliverable on this surface alone, and the harness
        matched it until now. Under one dispatcher entry the matching moves
        here, and `worktree-isolation` is what rides on it."""
        assert matches(profiles.CLAUDE, "EnterWorktree", "EnterWorktree")

    def test_the_negative_control_an_empty_row_does_not_match_everything(self) -> None:
        """An empty table must not become a matcher that says yes to all — the
        fallback is a NAME, not a wildcard."""
        assert not matches(profiles.CLAUDE, "Edit|Write|MultiEdit", "Bash")
        assert not matches(profiles.CLAUDE, "EnterWorktree", "Bash")


def test_every_shipped_matcher_is_covered_by_this_test() -> None:
    """A new shipped matcher must not join the library unnoticed by this file."""
    covered = {*_TERMINAL_MATCHERS, _MUTATION_MATCHER, "EnterWorktree"}
    shipped = {m for matchers in _shipped_matchers().values() for m in matchers}
    assert shipped, "no shipped matchers found — the reader, not the library, is broken"
    assert shipped <= covered, (
        f"shipped matchers this test does not exercise: {sorted(shipped - covered)}"
    )


@pytest.mark.parametrize("surface", sorted(LIVE))
def test_the_fan_out_name_is_the_one_the_chain_matches_on(surface: str) -> None:
    """codex fans `apply_patch` into per-file payloads BEFORE the chain matches,
    so asserting on the pre-fan-out name measured a path production does not
    take. Whatever the adapter produced is what gets matched here."""
    for native in FILE_MUTATION[surface]:
        call = LIVE[surface](native)
        assert matches(getattr(profiles, surface.upper()), _MUTATION_MATCHER, call.tool), (
            f"{surface}: the call the chain sees for {native!r} does not match"
        )
