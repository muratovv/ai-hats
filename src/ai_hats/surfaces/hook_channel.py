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
import os
import re
import sys
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

from ai_hats_core.deadline import Deadline

from ..constants import HOOK_POST_TOOL_USE, HOOK_PRE_TOOL_USE
from ..env import AI_HATS_PROJECT_DIR_ENV
from ..hook_exec import HookOutcomeKind, HookRun, HookVerdict, run_hook

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

    #: Put a question to the human at all.
    can_ask: bool
    #: Carry the question AND the rewritten input together. A ticketed question
    #: asked without its ticket approves the ORIGINAL command, so the two are
    #: one capability wherever a ticket is present.
    can_ask_with_ticket: bool
    #: Cancel a call that already ran, on PostToolUse.
    can_deny_after: bool
    #: Carry a nudge onward at all. Where it lands differs — the model's own
    #: context on most surfaces, an operator's console on one — but a surface
    #: with nowhere to put it drops the text rather than pretending.
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

    def spoken_name(self, native_tool: str) -> str:
        """The single name to write into a payload for ``native_tool``.

        A payload carries one name where a matcher may accept several, so this
        picks the first — and it is always one :meth:`matcher_names` accepts, or
        a hook would be handed a call its own matcher rejects.
        """
        return self.tool_names.get(native_tool, (native_tool,))[0]

    def manifest_path(self, cache_dir: Path) -> Path:
        return cache_dir.joinpath(*self.manifest_subpath, "hooks.json")

    def skills_root(self, cache_dir: Path) -> Path | None:
        if self.skills_subpath is None:
            return None
        return cache_dir.joinpath(*self.skills_subpath)


def matches(profile: SurfaceProfile, matcher: str, native_tool: str) -> bool:
    """Whether a composed row's ``matcher`` applies to this call.

    An unreadable matcher falls back to the alternatives it spells rather than
    to the whole string: a row nobody can compile still names the tools it meant
    to guard, and running that gate is the direction that fails safe.
    """
    if not matcher or matcher == "*":
        return True
    if not native_tool:
        # Nothing to filter ON. Filtering anyway would silently drop every gate
        # for a call whose payload we could not read — so run them all instead
        # and let each decide for itself.
        return True
    candidates = profile.matcher_names(native_tool)
    try:
        return any(re.fullmatch(matcher, name) is not None for name in candidates)
    except re.error:
        spelled = matcher.split("|")
        return any(name in spelled for name in candidates)


def speak_args(profile: SurfaceProfile, args: Mapping[str, object]) -> dict:
    """``args`` with the keys hooks defend against, never dropping what was there.

    An existing vocabulary key is left alone: the payload already said it, and
    overwriting it would hand the hook the surface's guess over its own word.
    """
    renamed = dict(args)
    for native, vocabulary in profile.arg_names.items():
        if native in renamed and vocabulary not in renamed:
            renamed[vocabulary] = renamed.pop(native)
    return renamed


def native_arg_keys(profile: SurfaceProfile, args: Mapping[str, object]) -> dict[str, str]:
    """Vocabulary key -> the key THIS payload used, for the keys it carried.

    The way back: a rewrite returned under the vocabulary name reaches a surface
    that looks for its own and finds none, running the original line instead.
    """
    return {profile.arg_names[key]: key for key in args if key in profile.arg_names}


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
    #: What the deciding hook exited with. `kind` is coarser — a surface whose
    #: own protocol IS the exit code has to hand back the number it was given.
    exit_code: int | None = None

    @property
    def gated(self) -> bool:
        """The agent may not proceed on its own."""
        return self.decision in (ChainDecision.DENY, ChainDecision.ASK)


#: What the whole chain for one tool call may spend. A request, not the
#: timeout: the surface's own bound is the ceiling above it, and it must be the
#: larger of the two or the dispatcher is killed before it can say anything.
HOOK_TIMEOUT_S: float = 60.0
HOOK_TIMEOUT_ENV = "AI_HATS_HOOK_TIMEOUT_S"

#: The name this bound had while only one channel of four offered it. Honoured
#: so a config that already sets it does not stop working in silence, which is
#: the failure mode this whole channel exists to remove.
RETIRED_TIMEOUT_ENVS = ("AI_HATS_AGY_HOOK_TIMEOUT_S",)

#: How much room the surface must leave above the chain, so a chain that spends
#: everything still reports instead of dying mid-sentence.
SURFACE_TIMEOUT_MARGIN_S: float = 30.0

#: What a hook's own gate cannot be reopened without.
GATE_BROKEN_ACK_ENV = "AI_HATS_GATE_BROKEN_ACK"

#: A reply must parse WHOLE, and the primitive returns a tail — which cuts a
#: JSON document's head off. Set far above the largest shipped reply measured.
REPLY_BYTES = 256 * 1024

_MATERIALIZATION_KINDS = frozenset(
    {
        HookOutcomeKind.SCRIPT_MISSING,
        HookOutcomeKind.NOT_EXECUTABLE,
        HookOutcomeKind.COMMAND_NOT_FOUND,
        HookOutcomeKind.EXEC_FAILED,
        HookOutcomeKind.LOG_UNUSABLE,
    }
)


def resolve_hook_timeout(environ: Mapping[str, str] | None = None) -> float:
    """The chain's budget: ``AI_HATS_HOOK_TIMEOUT_S`` or the default.

    Anything unusable falls back — a typo must not disarm the bound.
    """
    env = environ if environ is not None else os.environ
    raw = env.get(HOOK_TIMEOUT_ENV)
    if not raw:
        for retired in RETIRED_TIMEOUT_ENVS:
            raw = env.get(retired)
            if raw:
                sys.stderr.write(
                    f"ai-hats: {retired} is now {HOOK_TIMEOUT_ENV} and bounds every "
                    f"surface, not one — honouring it this run; rename it.\n"
                )
                break
    if not raw:
        return HOOK_TIMEOUT_S
    try:
        asked = float(raw)
    except ValueError:
        return HOOK_TIMEOUT_S
    return asked if asked > 0 else HOOK_TIMEOUT_S


def surface_timeout(environ: Mapping[str, str] | None = None) -> float:
    """What a surface must give its dispatcher, derived so the two cannot drift.

    Writing the surface's own number by hand is how codex came to bound the
    dispatcher and the hook at the same 60 seconds, which made the dispatcher's
    every timeout branch unreachable and left a killed chain with no verdict.
    """
    return resolve_hook_timeout(environ) + SURFACE_TIMEOUT_MARGIN_S


@dataclass(frozen=True)
class HookRow:
    """One composed row of the session manifest."""

    command: Path
    matcher: str
    tag: str


def _imposed(run: HookRun, row: HookRow, event: HookEvent | None) -> ChainVerdict | None:
    """The verdict ai-hats owes when the gate could not be DELIVERED.

    ``None`` when the hook ran and answered — that answer is between its author
    and whoever it stopped, and carries no hatch of ours.
    """
    if run.verdict in (HookVerdict.PASS, HookVerdict.REFUSE):
        return None
    out_of_time = (HookOutcomeKind.TIMED_OUT, HookOutcomeKind.NO_TIME_LEFT)
    hatch = HOOK_TIMEOUT_ENV if run.kind in out_of_time else GATE_BROKEN_ACK_ENV
    return ChainVerdict(
        decision=ChainDecision.DENY,
        reason=run.reason,
        hook=row.tag,
        event=event,
        stderr=run.stderr,
        hatch_env=hatch,
        kind=run.kind,
        exit_code=run.exit_code,
    )


def run_chain(
    profile: SurfaceProfile,
    *,
    event: HookEvent,
    rows: Sequence[HookRow],
    payloads: Sequence[dict],
    project_dir: Path,
    environ: Mapping[str, str] | None = None,
    log_dir: Path | None = None,
) -> ChainVerdict:
    """Run every row this call matches and return what the chain concluded.

    One deadline covers the whole call, so a chain cannot outlive the bound its
    surface gave the dispatcher; each hook draws its budget through it and a
    chain with nothing left refuses, naming the variable that widens it.
    """
    budget = resolve_hook_timeout(environ)
    deadline = Deadline.without_lock(budget, why=f"{profile.label} {event.value}")
    nudges: list[Nudge] = []
    for payload in payloads:
        tool = str(payload.get("tool_name", ""))
        for row in rows:
            if not matches(profile, row.matcher, tool):
                continue
            run = run_hook(
                row.command,
                point=f"{profile.label}:{event.value}",
                budget=budget,
                deadline=deadline,
                project_dir=project_dir,
                stdin_payload=json.dumps(payload).encode("utf-8"),
                tail_bytes=REPLY_BYTES,
                log_path=(
                    None if log_dir is None else log_dir / f"{event.value}-{row.command.name}.log"
                ),
            )
            imposed = _imposed(run, row, event)
            if imposed is not None:
                return replace(imposed, nudges=tuple(nudges))
            try:
                reply = parse_reply(
                    run.said,
                    exit_code=run.exit_code if run.exit_code is not None else 0,
                    hook=row.tag,
                    stderr=run.stderr,
                    truncated=run.truncated,
                )
            except ReplyUnreadable as exc:
                return ChainVerdict(
                    decision=ChainDecision.DENY,
                    reason=f"{row.tag}: {exc}",
                    hook=row.tag,
                    nudges=tuple(nudges),
                    event=event,
                    stderr=run.stderr,
                    hatch_env=GATE_BROKEN_ACK_ENV,
                )
            if reply.nudge is not None:
                nudges.append(reply.nudge)
            if reply.decision in (ChainDecision.DENY, ChainDecision.ASK):
                return ChainVerdict(
                    decision=reply.decision,
                    reason=reply.reason or f"blocked by {row.tag}",
                    hook=row.tag,
                    nudges=tuple(nudges),
                    updated_input=reply.updated_input,
                    event=reply.event or event,
                    stderr=run.stderr,
                    exit_code=run.exit_code,
                )
    return ChainVerdict(decision=ChainDecision.ALLOW, nudges=tuple(nudges), event=event)


def project_dir_from(environ: Mapping[str, str] | None = None) -> Path:
    """The project the gates must inspect, from the pin the launcher wrote."""
    env = environ if environ is not None else os.environ
    pinned = env.get(AI_HATS_PROJECT_DIR_ENV)
    return Path(pinned) if pinned else Path.cwd()


def worded(verdict: ChainVerdict) -> str:
    """The refusal a human reads, with the way out it leaves open.

    A gate's own refusal needs no hatch — arguing with it is between its author
    and whoever it stopped. Everything ai-hats imposed owes a named exit, or it
    only teaches the reader to reach for the switch that disables everything.
    """
    if not verdict.hatch_env:
        return verdict.reason
    if verdict.hatch_env == HOOK_TIMEOUT_ENV:
        way_out = f"the gate hit its budget — raise {verdict.hatch_env} if it needs longer"
    else:
        way_out = f"this gate could not run — set {verdict.hatch_env}=1 to proceed past it"
    return f"{verdict.reason}\nai-hats: {way_out}"


def to_wire(verdict: ChainVerdict) -> dict:
    """The verdict as a document, for a channel that cannot hold the value.

    THIS is the contract an out-of-process channel is given: it marshals this
    back instead of deriving a verdict of its own from an exit code, which is
    how a channel comes to understand only the shapes it happened to implement.
    """
    return {
        "decision": verdict.decision.value,
        "reason": worded(verdict),
        "hook": verdict.hook,
        "nudges": [{"text": n.text, "hook": n.hook} for n in verdict.nudges],
        "updated_input": verdict.updated_input,
        "hatch_env": verdict.hatch_env,
    }


def reduce_to(dialect: Dialect, verdict: ChainVerdict) -> ChainVerdict:
    """The same verdict, reduced to what this surface can actually utter.

    Every reduction applies; a surface that can neither ask nor carry a nudge
    needs both, and returning after the first would leave the second in place.
    """
    reduced = verdict
    if reduced.decision is ChainDecision.ASK:
        # A ticketed question asked without its ticket approves the original
        # command; a question this surface cannot put at all is not a question.
        loses_ticket = bool(reduced.updated_input) and not dialect.can_ask_with_ticket
        if loses_ticket or not dialect.can_ask:
            reduced = replace(
                reduced,
                decision=ChainDecision.DENY,
                reason=(
                    f"{reduced.reason or 'this call needs explicit consent'}; "
                    "this surface cannot carry the consent this gate asked for, "
                    "so grant it outside this tool call and retry"
                ),
                updated_input=None,
            )
    if reduced.nudges and not dialect.can_carry_nudges:
        reduced = replace(reduced, nudges=())
    return reduced


__all__ = [
    "to_wire",
    "RETIRED_TIMEOUT_ENVS",
    "project_dir_from",
    "worded",
    "surface_timeout",
    "native_arg_keys",
    "speak_args",
    "run_chain",
    "resolve_hook_timeout",
    "SURFACE_TIMEOUT_MARGIN_S",
    "REPLY_BYTES",
    "HookRow",
    "GATE_BROKEN_ACK_ENV",
    "HOOK_TIMEOUT_S",
    "HOOK_TIMEOUT_ENV",
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
