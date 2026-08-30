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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ai_hats_core.deadline import Deadline

from ..constants import HOOK_POST_TOOL_USE, HOOK_PRE_TOOL_USE
from ..env import (
    AI_HATS_PROJECT_DIR_ENV,
    ENV_GATE_BROKEN_ACK,
    ENV_HOOK_SURFACE_TIMEOUT_MS,
    ENV_HOOK_TIMEOUT_S,
    HOOK_TIMEOUT,
    ENV_RETIRED_AGY_HOOK_TIMEOUT_S,
    Budget,
    read_budget,
)
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


#: Every bindable event, as the names a manifest and a config use. Derived from
#: the enum so a surface's own list cannot fall behind it in silence.
BINDABLE_EVENTS: tuple[str, ...] = tuple(e.value for e in HookEvent)


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
    #: Every event this surface will actually deliver, in its own names. Usually
    #: :data:`BINDABLE_EVENTS`; a surface with an arrival of its own says so here
    #: rather than spelling the whole set out again somewhere else.
    native_events: tuple[str, ...]
    #: Where the manifest sits under the session cache dir.
    manifest_subpath: tuple[str, ...]
    #: Where the session skills mirror sits under the cache dir; ``None`` when
    #: only the manifest knows, which is the one honest answer for a surface
    #: whose mirror lives outside the cache entirely.
    skills_subpath: tuple[str, ...] | None
    #: What this surface can utter on an ordinary arrival.
    speaks: Dialect
    #: Arrivals whose dialect is NARROWER than the row above, by native name.
    #: An event axis, because one surface has one: codex may put a question only
    #: on `PermissionRequest`, and the reply there has no slot for advice.
    speaks_on: Mapping[str, Dialect] = field(default_factory=dict)
    #: The status a refusal ai-hats IMPOSED exits with. Zero on a surface that
    #: acts on the document alone; codex reads 2 as its refusal, agy 1.
    imposed_status: int = 0
    #: Whether a refusal a HOOK uttered carries the child's own exit code out.
    #: True on the one surface whose protocol IS the status (agy, HATS-1598).
    forwards_hook_status: bool = False

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

    def dialect(self, native_event: str) -> Dialect:
        """What this surface can utter on ``native_event``.

        Known before any hook arrives, so it is read from the row rather than
        decided while one is being judged.
        """
        return self.speaks_on.get(native_event, self.speaks)

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
    body = stdout.strip()
    if exit_code == REFUSAL_EXIT and not (body and not truncated and body.startswith("{")):
        # Exit 2 is the refusal for a hook with no JSON to hand back. One that
        # DID hand some back is read below, or its ticket goes with the throw.
        return HookReply(
            decision=ChainDecision.DENY,
            reason=stderr.strip() or "blocked by an ai-hats guard",
        )
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
HOOK_TIMEOUT_S: float = HOOK_TIMEOUT.default
HOOK_TIMEOUT_ENV = ENV_HOOK_TIMEOUT_S

#: The name this bound had while only one channel of four offered it. Honoured
#: so a config that already sets it does not stop working in silence, which is
#: the failure mode this whole channel exists to remove.
RETIRED_TIMEOUT_ENVS = (ENV_RETIRED_AGY_HOOK_TIMEOUT_S,)

#: The retired spellings, read under the live budget's own default and doc.
_RETIRED_BUDGETS = tuple(
    Budget(name, HOOK_TIMEOUT.default, HOOK_TIMEOUT.doc) for name in RETIRED_TIMEOUT_ENVS
)

#: Said once per process, which is once per tool call — the dispatcher is a
#: fresh process each time. Twice a call, from both resolvers, was noise.
_RENAME_SAID: set[str] = set()

#: How much room the surface must leave above the chain, so a chain that spends
#: everything still reports instead of dying mid-sentence.
SURFACE_TIMEOUT_MARGIN_S: float = 30.0

#: What a hook's own gate cannot be reopened without.
GATE_BROKEN_ACK_ENV = ENV_GATE_BROKEN_ACK

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
    if env.get(HOOK_TIMEOUT_ENV):
        return read_budget(HOOK_TIMEOUT, env)
    for retired in _RETIRED_BUDGETS:
        if not env.get(retired.name):
            continue
        if retired.name not in _RENAME_SAID:
            _RENAME_SAID.add(retired.name)
            sys.stderr.write(
                f"ai-hats: {retired.name} is now {HOOK_TIMEOUT_ENV} and bounds every "
                f"surface, not one — honouring it this run; rename it.\n"
            )
        return read_budget(retired, env)
    return HOOK_TIMEOUT.default


def surface_timeout(environ: Mapping[str, str] | None = None) -> float:
    """What a surface must give its dispatcher, derived so the two cannot drift.

    Writing the surface's own number by hand is how codex came to bound the
    dispatcher and the hook at the same 60 seconds, which made the dispatcher's
    every timeout branch unreachable and left a killed chain with no verdict.

    Read by the two surfaces that impose an outer bound at all: codex writes it
    into its TOML, and opencode is handed it through
    :data:`ENV_HOOK_SURFACE_TIMEOUT_MS` because its plugin is copied verbatim.
    agy's settings entry and cline's ``--hooks-dir`` shim carry no timeout
    field, so their dispatcher is bounded only from the inside — which is the
    safe direction: the chain's own deadline always leaves it alive to answer.
    """  # comment-length: allow — which surfaces impose an outer bound is the contract
    return resolve_hook_timeout(environ) + SURFACE_TIMEOUT_MARGIN_S


@dataclass(frozen=True)
class HookCall:
    """One call as the chain sees it — the payload, and the tool's native name.

    The two carry different names on purpose: the payload spells the tool in the
    matcher vocabulary a hook reads, while a matcher may be written in the
    surface's own. Keeping the native name beside the payload is what makes
    :meth:`SurfaceProfile.matcher_names` reachable rather than aspirational —
    matching on the collapsed name alone means `Create` no longer answers to a
    `Write` matcher on agy, though the row says it should.
    """  # comment-length: allow — why a call carries two names is the contract

    payload: dict
    native_tool: str = ""

    @property
    def tool(self) -> str:
        """The name to match on: the surface's own where it is known."""
        return self.native_tool or str(self.payload.get("tool_name", ""))


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


def _skipped_by_hatch(run: HookRun, row: HookRow, environ: Mapping[str, str]) -> str | None:
    """Why this delivery failure is skipped rather than refused, or ``None``.

    The hatch is read HERE, beside the refusal it opens, for the reason the git
    channel states at ``githooks_run._skip_reason``: the deny-names-its-hatch
    invariant is worth nothing if the flag it names is inert (HATS-1253 P4).

    Only a gate ai-hats could not MATERIALIZE qualifies. A timeout keeps its own
    bound to raise, and opening it here would turn a hang into a pass.
    """
    if run.kind not in _MATERIALIZATION_KINDS:
        return None
    if not environ.get(GATE_BROKEN_ACK_ENV):
        return None
    return f"{GATE_BROKEN_ACK_ENV} set — '{row.tag}' SKIPPED: {run.reason}"


def record_gate_skipped(reason: str, *, hook: str, project_dir: Path) -> None:
    """Say — on stderr AND in the bypass journal — that a gate was skipped.

    Passing a gate is allowed; passing one SILENTLY is not (ADR-0020 D2). The
    journal is written in-process, the way ``consent_port.journal_sink`` does:
    this channel already runs inside the ai-hats interpreter, so the git
    channel's reason for spawning a writer does not apply.
    """
    sys.stderr.write(f"ai-hats: gate SKIPPED (hatch) — {reason}\n")
    try:
        from ai_hats_library.hooks.bypass_journal import journal_bypass
    except ImportError as exc:
        sys.stderr.write(f"ai-hats: hatch NOT RECORDED ({reason}) — {exc}\n")
        return
    # Never raises and reports its own failure on stderr, so a lost record is
    # loud without this call having to re-check it.
    journal_bypass("hatch", reason, hook=hook, cwd=project_dir)


def undeliverable(
    reason: str,
    *,
    event: HookEvent | None = None,
    hook: str = "",
    project_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ChainVerdict:
    """The verdict when the gate SET could not be delivered — no gate ran at all.

    The sibling of :func:`_imposed`, one level up: that one answers for a single
    hook that would not start, this one for a manifest that never resolved. Both
    owe the same named exit, and both must honour it — a refusal every surface
    words identically and only one of them can open is the failure this channel
    exists to remove.
    """  # comment-length: allow — the two levels of delivery failure are the contract
    env = environ if environ is not None else os.environ
    if env.get(GATE_BROKEN_ACK_ENV):
        record_gate_skipped(
            f"{GATE_BROKEN_ACK_ENV} set — SKIPPED: {reason}",
            hook=hook or "manifest",
            project_dir=project_dir if project_dir is not None else Path.cwd(),
        )
        return ChainVerdict(decision=ChainDecision.ALLOW, event=event)
    return ChainVerdict(
        decision=ChainDecision.DENY,
        reason=reason,
        hook=hook,
        event=event,
        hatch_env=GATE_BROKEN_ACK_ENV,
    )


#: How many gates may be in flight at once. The trade-off, both ways:
#:
#: * **Raising it** buys wall time only while jobs exceed it. A surface starts
#:   every matched hook together (measured on claude 2.1.247), so a cap below
#:   the job count turns max-time back into sum-time, in steps of
#:   ``ceil(jobs / this)``.
#: * **Lowering it** bounds the spawn. A job is a subprocess and the count is
#:   rows x payloads, so a surface that fans one call into several (cline per
#:   command, codex per patched file) multiplies it — unbounded, a wide fan-out
#:   is a fork storm on every tool call.
#:
#: Eight clears the shipped set with room: three of the eight shipped gates
#: match the busiest single call, so the cap binds only past a three-way
#: fan-out. Not configurable until a surface is measured needing another number.
HOOK_PARALLELISM = 8


def _matched(
    profile: SurfaceProfile, rows: Sequence[HookRow], calls: Sequence[HookCall]
) -> list[tuple[HookCall, HookRow]]:
    """Every (call, row) pair this chain owes a run, in the order it reports them."""
    return [
        (call, row) for call in calls for row in rows if matches(profile, row.matcher, call.tool)
    ]


def _run_matched(
    jobs: Sequence[tuple[HookCall, HookRow]],
    *,
    profile: SurfaceProfile,
    event: HookEvent,
    budget: float,
    deadline: Deadline,
    project_dir: Path,
    log_dir: Path | None,
) -> list[HookRun]:
    """Run every matched gate, together, and return their outcomes in job order.

    Order is the reporting order, not the finishing order: what the chain
    CONCLUDES must not depend on which gate happened to answer first.
    """

    def one(indexed: tuple[int, tuple[HookCall, HookRow]]) -> HookRun:
        position, (call, row) = indexed
        return run_hook(
            row.command,
            point=f"{profile.label}:{event.value}",
            budget=budget,
            deadline=deadline,
            project_dir=project_dir,
            stdin_payload=json.dumps(call.payload).encode("utf-8"),
            tail_bytes=REPLY_BYTES,
            log_path=(
                None
                if log_dir is None
                # Position, not just the command: one row runs once per payload,
                # and a shared path is two writers truncating each other.
                else log_dir / f"{event.value}-{position}-{row.command.name}.log"
            ),
        )

    if len(jobs) < 2:
        return [one(job) for job in enumerate(jobs)]
    with ThreadPoolExecutor(max_workers=min(len(jobs), HOOK_PARALLELISM)) as pool:
        return list(pool.map(one, enumerate(jobs)))


def _concluded(
    jobs: Sequence[tuple[HookCall, HookRow]],
    runs: Sequence[HookRun],
    *,
    event: HookEvent,
    environ: Mapping[str, str],
    project_dir: Path,
) -> ChainVerdict:
    """What the chain concluded, walked in row order over gates that ALL ran.

    The walk stops at the first gate that objected, so the verdict is the one
    the sequential chain reported. What it does NOT undo is that the gates
    behind it ran: their side effects happened, and their stderr is carried.
    """
    # Every gate's, decider's and the ones behind it included: they ran, and an
    # allowing gate's note — the bypass journal's own "NOT RECORDED" among them —
    # has no other trace at all.
    said = "".join(run.stderr for run in runs)
    nudges: list[Nudge] = []
    for (_, row), run in zip(jobs, runs):
        imposed = _imposed(run, row, event)
        if imposed is not None:
            skipped = _skipped_by_hatch(run, row, environ)
            if skipped is not None:
                record_gate_skipped(skipped, hook=row.tag, project_dir=project_dir)
                continue
            return replace(imposed, nudges=tuple(nudges), stderr=said)
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
                stderr=said,
                hatch_env=GATE_BROKEN_ACK_ENV,
            )
        if reply.nudge is not None and reply.nudge not in nudges:
            # A surface fans one call into several payloads (cline per
            # command, codex per patched file); the hook still said it once.
            nudges.append(reply.nudge)
        if reply.decision in (ChainDecision.DENY, ChainDecision.ASK):
            return ChainVerdict(
                decision=reply.decision,
                reason=reply.reason or f"blocked by {row.tag}",
                hook=row.tag,
                nudges=tuple(nudges),
                updated_input=reply.updated_input,
                # The dispatcher's, never the child's: four shipped scripts
                # hardcode `PreToolUse` in their reply, and letting that
                # rename the event steers the reductions below it.
                event=event,
                stderr=said,
                exit_code=run.exit_code,
            )
    return ChainVerdict(
        decision=ChainDecision.ALLOW,
        nudges=tuple(nudges),
        event=event,
        stderr=said,
    )


def run_chain(
    profile: SurfaceProfile,
    *,
    event: HookEvent,
    rows: Sequence[HookRow],
    calls: Sequence[HookCall],
    project_dir: Path,
    environ: Mapping[str, str] | None = None,
    log_dir: Path | None = None,
) -> ChainVerdict:
    """Run every row this call matches and return what the chain concluded.

    The matched gates run TOGETHER and the conclusion is drawn afterwards, in
    row order: the surface runs its own hooks that way, and a chain answering in
    sum-time cannot stay inside the budget the surface allowed it. What changes
    is which gates RUN — every matched one now does, so a gate with a side
    effect behind an objector fires where it used to be skipped — not what the
    chain says, which is still the first objector in row order.

    One deadline covers the whole call, so a chain cannot outlive the bound its
    surface gave the dispatcher; each hook draws its budget through it and a
    chain with nothing left refuses, naming the variable that widens it.
    """  # comment-length: allow — what parallelism changes and what it does not is the contract
    env = environ if environ is not None else os.environ
    budget = resolve_hook_timeout(environ)
    deadline = Deadline.without_lock(budget, why=f"{profile.label} {event.value}")
    jobs = _matched(profile, rows, calls)
    runs = _run_matched(
        jobs,
        profile=profile,
        event=event,
        budget=budget,
        deadline=deadline,
        project_dir=project_dir,
        log_dir=log_dir,
    )
    return _concluded(jobs, runs, event=event, environ=env, project_dir=project_dir)


def status_for(profile: SurfaceProfile, verdict: ChainVerdict) -> int:
    """The exit status this verdict earns on this surface.

    The document is the answer on every surface; a status is a projection of it
    that two of them additionally act on. Which status is a row of the profile,
    so the same verdict cannot mean one thing here and another there.
    """
    if verdict.decision is not ChainDecision.DENY:
        return 0
    if profile.forwards_hook_status and verdict.exit_code not in (None, 0):
        return verdict.exit_code or 0
    # Only what ai-hats imposed: a refusal a hook UTTERED is its author's, and
    # inventing a status for it would overrule them.
    return profile.imposed_status if verdict.hatch_env else 0


def relay_stderr(verdict: ChainVerdict) -> None:
    """Put back what the hooks said on stderr, whatever the verdict was.

    Their diagnostics are theirs to make. A surface that relays them only behind
    a refusal loses exactly the notes an ALLOWING gate left — the bypass
    journal's own "NOT RECORDED" among them — which have no other trace at all.
    """
    if verdict.stderr:
        sys.stderr.write(verdict.stderr)


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


def _consent_cannot_follow(dialect: Dialect, verdict: ChainVerdict) -> bool:
    """A question this surface cannot put, or cannot put WITH its ticket.

    A ticketed question asked without its ticket approves the original command,
    so the two are one capability wherever a ticket is present.
    """
    if verdict.decision is not ChainDecision.ASK:
        return False
    return not dialect.can_ask or (bool(verdict.updated_input) and not dialect.can_ask_with_ticket)


def _refuse_instead(verdict: ChainVerdict) -> ChainVerdict:
    return replace(
        verdict,
        decision=ChainDecision.DENY,
        reason=(
            f"{verdict.reason or 'this call needs explicit consent'}; "
            "this surface cannot carry the consent this gate asked for, "
            "so grant it outside this tool call and retry"
        ),
        # The rewrite went with the question; keeping it would hand the surface
        # an input nobody approved.
        updated_input=None,
    )


def _refusal_arrives_too_late(dialect: Dialect, verdict: ChainVerdict) -> bool:
    """A refusal for a call that already ran, which this surface can still SAY.

    Turning it into something the reader sees needs somewhere to put it. Without
    that, the refusal stays a refusal and the surface's own reply decides what
    becomes of it — inventing an allow here would be manufacturing the fail-open
    this channel exists to remove.
    """
    return (
        verdict.decision is ChainDecision.DENY
        and verdict.event is HookEvent.POST_TOOL_USE
        and not dialect.can_deny_after
        and dialect.can_carry_nudges
    )


def _tell_instead(verdict: ChainVerdict) -> ChainVerdict:
    """Say it rather than drop it: the gate looked and objected, and the reader
    is the only one who can still act on that.

    The text told is the WORDED refusal, so a delivery failure keeps the way
    past it — a reader handed the bare reason gets the half they cannot act on.
    The hatch is cleared with it, having already been said.
    """
    return replace(
        verdict,
        decision=ChainDecision.ALLOW,
        nudges=(*verdict.nudges, Nudge(worded(verdict), verdict.hook)),
        reason="",
        hatch_env="",
    )


def _nudge_has_nowhere_to_go(dialect: Dialect, verdict: ChainVerdict) -> bool:
    return bool(verdict.nudges) and not dialect.can_carry_nudges


def _drop_nudges(verdict: ChainVerdict) -> ChainVerdict:
    return replace(verdict, nudges=())


@dataclass(frozen=True)
class Reduction:
    """One thing a surface cannot utter, and what it becomes instead."""

    name: str
    needed: Callable[[Dialect, ChainVerdict], bool]
    apply: Callable[[ChainVerdict], ChainVerdict]


#: Applied to a fixed point, never in sequence. One reduction can produce what
#: another must then handle — a refusal that becomes a nudge on a surface that
#: also cannot carry nudges — and an if-chain gets that right only in the order
#: it happens to be written, which is how a dropped nudge survived review once.
REDUCTIONS: tuple[Reduction, ...] = (
    Reduction("consent this surface cannot carry", _consent_cannot_follow, _refuse_instead),
    Reduction("a refusal that arrives too late", _refusal_arrives_too_late, _tell_instead),
    Reduction("a nudge with nowhere to go", _nudge_has_nowhere_to_go, _drop_nudges),
)


def reduce_to(
    dialect: Dialect,
    verdict: ChainVerdict,
    *,
    reductions: Sequence[Reduction] = REDUCTIONS,
) -> ChainVerdict:
    """The same verdict, reduced to what this surface can actually utter.

    ``reductions`` is a parameter so the order-independence above can be checked
    by handing in a permutation, rather than by reaching into this module and
    swapping the tuple out underneath it.
    """
    reduced = verdict
    # One pass per reduction is enough for any set where each removes what it
    # matches: the bound is what makes a mutually-undoing pair loud rather than
    # a hang.
    for _ in range(len(reductions) + 1):
        for reduction in reductions:
            if reduction.needed(dialect, reduced):
                reduced = reduction.apply(reduced)
                break
        else:
            return reduced
    raise RuntimeError(
        f"reductions did not settle for {dialect}: {[r.name for r in reductions]} "
        f"— two of them undo each other"
    )


__all__ = [
    "REDUCTIONS",
    "Reduction",
    "BINDABLE_EVENTS",
    "record_gate_skipped",
    "relay_stderr",
    "undeliverable",
    "to_wire",
    "RETIRED_TIMEOUT_ENVS",
    "project_dir_from",
    "worded",
    "surface_timeout",
    "native_arg_keys",
    "speak_args",
    "run_chain",
    "status_for",
    "resolve_hook_timeout",
    "SURFACE_TIMEOUT_MARGIN_S",
    "ENV_HOOK_SURFACE_TIMEOUT_MS",
    "REPLY_BYTES",
    "HookCall",
    "HookRow",
    "GATE_BROKEN_ACK_ENV",
    "HOOK_PARALLELISM",
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
