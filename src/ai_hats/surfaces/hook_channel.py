"""The one hook channel every surface speaks through — ADR-0020 D2.

A surface differs from its siblings in NAMES and SHAPES, not in policy. Those
travel as :class:`SurfaceProfile` data, each row kept next to its own surface,
while everything that DECIDES lives here.

The vocabulary a ``SKILL.md`` matcher is written in is the **matcher
vocabulary** — not any one surface's, even where a surface's native names
already spell it and need no translation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Mapping

from ..constants import HOOK_POST_TOOL_USE, HOOK_PRE_TOOL_USE
from ..hook_exec import HookOutcomeKind

#: The three matcher-vocabulary names every shipped file-mutation row treats as
#: one class, so a profile states the class rather than restating the trio.
FILE_MUTATION_NAMES = ("Edit", "Write", "MultiEdit")


class HookEvent(Enum):
    """A point a composed hook can be bound to.

    Closed at two because ``RUNTIME_HOOK_EVENTS`` is: a surface with a third
    native arrival maps it onto one of these before dispatch, so its own
    vocabulary stops at its own edge.
    """

    PRE_TOOL_USE = HOOK_PRE_TOOL_USE
    POST_TOOL_USE = HOOK_POST_TOOL_USE

    @classmethod
    def parse(cls, name: str) -> HookEvent | None:
        """The event ``name`` denotes, or ``None`` when it denotes none."""
        try:
            return cls(name)
        except ValueError:
            return None


class ChainDecision(Enum):
    """What the composed chain concluded for one tool call."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class Nudge:
    """Advice fed back to the model. Never touches a decision.

    Carries its author because a chain joins nudges from several hooks, and a
    reader handed the joined text cannot tell whose advice it is reading.
    """

    text: str
    hook: str = ""


@dataclass(frozen=True)
class Dialect:
    """Which verdicts a surface can utter natively.

    A verdict it cannot utter is downgraded once, here, so the downgrade text
    exists in one copy rather than one per surface.
    """

    #: Ask the human AND put the rewritten input on the call, atomically. One
    #: capability, not two: a question without the ticket approves the original
    #: command, which is a broken consent rather than a degraded one.
    can_ask_with_ticket: bool
    #: Cancel a call that already ran, on PostToolUse.
    can_deny_after: bool
    #: Carry a nudge back to the model.
    can_carry_nudges: bool


@dataclass(frozen=True)
class SurfaceProfile:
    """One surface, as data. No branch of policy belongs in here."""

    label: str
    #: Native tool name -> the matcher-vocabulary names it answers to. An empty
    #: row is how a whole class of gates goes quiet on a surface.
    tool_names: Mapping[str, tuple[str, ...]]
    #: Native argument name -> the matcher-vocabulary name hooks defend against.
    arg_names: Mapping[str, str]
    #: Where the manifest sits under the session cache dir.
    manifest_subpath: tuple[str, ...]
    #: Where the session skills mirror sits under the cache dir; ``None`` when
    #: only the manifest knows, which is the one honest answer for a surface
    #: whose mirror lives outside the cache entirely.
    skills_subpath: tuple[str, ...] | None
    speaks: Dialect

    def matcher_names(self, native_tool: str) -> tuple[str, ...]:
        """Every name a matcher may use for ``native_tool`` on this surface.

        The native name stays a candidate so a matcher written in the surface's
        own vocabulary keeps working.
        """
        mapped = self.tool_names.get(native_tool, ())
        return (native_tool, *mapped) if native_tool else tuple(mapped)

    def manifest_path(self, cache_dir: Path) -> Path:
        return cache_dir.joinpath(*self.manifest_subpath, "hooks.json")

    def skills_root(self, cache_dir: Path) -> Path | None:
        if self.skills_subpath is None:
            return None
        return cache_dir.joinpath(*self.skills_subpath)


def matches(profile: SurfaceProfile, matcher: str, native_tool: str) -> bool:
    """Whether a composed row's ``matcher`` applies to this call."""
    if not matcher or matcher == "*":
        return True
    candidates = profile.matcher_names(native_tool)
    if not candidates:
        return False
    try:
        return any(re.fullmatch(matcher, name) is not None for name in candidates)
    except re.error:
        return matcher in candidates


class ReplyUnreadable(ValueError):
    """The hook answered and the answer could not be read.

    Distinct from "the hook said nothing": silence is an allow, an unreadable
    answer is a gate whose verdict was lost, which is the fail-open this channel
    exists to remove.
    """


@dataclass(frozen=True)
class HookReply:
    """One hook's answer, in the single dialect every shipped hook speaks."""

    #: ``None`` when the hook finished without naming a decision. Silence still
    #: lets the call through, but a gate that looked and allowed is a different
    #: fact from no gate having an opinion, and only one of them is evidence.
    decision: ChainDecision | None = None
    reason: str = ""
    nudge: Nudge | None = None
    updated_input: dict | None = None
    #: The event the hook answered under, when it named one.
    event: HookEvent | None = None


#: Exit 2 is the provider-agnostic refusal, for a hook with no JSON to hand back
#: (ADR-0020 D2). Its reason reaches us on stderr and nowhere else.
REFUSAL_EXIT = 2

_DECISIONS = {d.value: d for d in ChainDecision}


def parse_reply(
    stdout: str,
    *,
    exit_code: int,
    hook: str = "",
    stderr: str = "",
    truncated: bool = False,
) -> HookReply:
    """Read one hook's answer. Raises :class:`ReplyUnreadable` when it cannot.

    ``truncated`` says the transport kept only a tail of the child's output. A
    tail cuts a JSON document's HEAD, so a truncated body can never be parsed
    and must not be mistaken for a hook that stayed silent.
    """
    if exit_code == REFUSAL_EXIT:
        return HookReply(
            decision=ChainDecision.DENY,
            reason=stderr.strip() or "blocked by an ai-hats guard",
        )
    body = stdout.strip()
    if not body:
        if truncated:
            raise ReplyUnreadable("the hook's answer was truncated away entirely")
        return HookReply()
    if truncated:
        raise ReplyUnreadable(f"the hook's answer was truncated to {len(body)} bytes")
    try:
        raw = json.loads(body)
    except ValueError as exc:
        raise ReplyUnreadable(f"the hook's answer is not readable JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReplyUnreadable(f"the hook's answer is {type(raw).__name__}, not an object")
    return _read(raw, hook)


def _read(raw: dict, hook: str) -> HookReply:
    spoken = raw.get("hookSpecificOutput")
    spoken = spoken if isinstance(spoken, dict) else {}
    decision = _DECISIONS.get(str(spoken.get("permissionDecision", "")).lower())
    reason = str(spoken.get("permissionDecisionReason", ""))
    updated = spoken.get("updatedInput")
    # The top-level pair is the PostToolUse spelling of a refusal; it wins
    # because a hook emitting both means the second to be read.
    if raw.get("decision") == "block":
        decision = ChainDecision.DENY
        reason = str(raw.get("reason", ""))
    text = str(spoken.get("additionalContext", ""))
    return HookReply(
        decision=decision,
        reason=reason,
        nudge=Nudge(text, hook) if text else None,
        updated_input=updated if isinstance(updated, dict) else None,
        event=HookEvent.parse(str(spoken.get("hookEventName", ""))),
    )


@dataclass(frozen=True)
class ChainVerdict:
    """What the whole composed chain said, as one value."""

    decision: ChainDecision
    reason: str = ""
    #: Tag of the deciding hook; empty when nothing objected.
    hook: str = ""
    #: Every nudge the chain fed back, each still naming its author.
    nudges: tuple[Nudge, ...] = ()
    updated_input: dict | None = None
    #: The event this verdict answers under.
    event: HookEvent | None = None
    #: The child's own stderr — a load-bearing channel, not a diagnostic one:
    #: the bypass journal and an exit-2 refusal's reason travel on it alone.
    stderr: str = ""
    #: Name of the variable that lets a human proceed when ai-hats itself could
    #: not deliver the gate. DERIVED FROM ``kind``, never from a hook's words —
    #: no hook emits a machine-readable hatch, they all spell it in prose.
    hatch_env: str = ""
    #: The fact behind a verdict ai-hats imposed rather than a hook uttering it.
    kind: HookOutcomeKind | None = None

    @property
    def gated(self) -> bool:
        """The agent may not proceed on its own."""
        return self.decision in (ChainDecision.DENY, ChainDecision.ASK)


def reduce_to(dialect: Dialect, verdict: ChainVerdict) -> ChainVerdict:
    """The same verdict, reduced to what this surface can actually utter.

    Every reduction applies; a surface that can neither ask nor carry a nudge
    needs both, and returning after the first would leave the second in place.
    """
    reduced = verdict
    if reduced.decision is ChainDecision.ASK and not dialect.can_ask_with_ticket:
        reduced = replace(
            reduced,
            decision=ChainDecision.DENY,
            reason=(
                f"{reduced.reason or 'this call needs explicit consent'}; "
                "this surface cannot ask for consent or carry a rewritten "
                "command, so grant it outside this tool call and retry"
            ),
            # The rewrite went with the question; keeping it would hand the
            # surface an input nobody approved.
            updated_input=None,
        )
    if reduced.nudges and not dialect.can_carry_nudges:
        reduced = replace(reduced, nudges=())
    return reduced


__all__ = [
    "FILE_MUTATION_NAMES",
    "ChainDecision",
    "ChainVerdict",
    "Dialect",
    "HookEvent",
    "HookReply",
    "Nudge",
    "REFUSAL_EXIT",
    "ReplyUnreadable",
    "SurfaceProfile",
    "matches",
    "parse_reply",
    "reduce_to",
]
