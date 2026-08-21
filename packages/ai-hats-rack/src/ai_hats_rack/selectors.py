"""The rack's own point grammar: an arrow, and what it selects (HATS-1719).

Three entities the word "point" used to name at once (ADR-0017 §3): an **event**
is one happening — the pair ``(from, to)``; a **selector** is what stands in
``at:`` and denotes a SET of events; a **row** is the whole declaration. This
module owns the middle one, so nobody else has to spell the grammar.

Derivability is deliberately wider than legality. :func:`parse_selector` derives
every arrow, which is what lets a refusal say *what* is wrong; :func:`selector_form`
decides what is legal today and names the card that opens the rest.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

#: Reserved. Upper-case because state names in every shipped topology are
#: lower-case, so the case alone makes the word not a name.
ANY = "ANY"
NONE = "NONE"

ARROW = "->"

#: What a state name may be made of. A POSITIVE charset, not a blocklist: the
#: refusals this narrows are typos nobody spells the same way twice
#: (``a->>b``, a zero-width space before the arrow), and enumerating them was
#: how three of them got through review.
_STATE_CHARS = frozenset(string.ascii_letters + string.digits + "_.-")


@dataclass(frozen=True)
class Edge:
    """One event: the pair a transition takes."""

    from_state: str
    to_state: str


@dataclass(frozen=True)
class Selector:
    """A set of events, denoted by an arrow. ``ANY`` on a side means "any state".

    Matching is a predicate over the pair — never a string comparison. That is
    what lets one selector answer for eight edges without anyone expanding it
    into eight strings first (design.md §1.5).
    """

    source: str
    target: str

    def matches(self, edge: Edge) -> bool:
        return (self.source in (ANY, edge.from_state)) and (self.target in (ANY, edge.to_state))

    def __str__(self) -> str:
        """The canonical spelling: an ``ANY`` side is written empty, unless both
        are — "everywhere" is spelled with the word (design.md §1.3)."""
        if self.source == ANY and self.target == ANY:
            return f"{ANY}{ARROW}{ANY}"
        source = "" if self.source == ANY else self.source
        target = "" if self.target == ANY else self.target
        return f"{source}{ARROW}{target}"


#: Every event, whatever the pair — the value behind ``ANY->ANY``. Named once so a
#: subscriber that means "every move of this backlog" says it instead of rebuilding
#: the topology's state product to spell it (HATS-1720).
EVERYWHERE = Selector(ANY, ANY)


#: The spelling this grammar replaced. Recognised on purpose: it must be REFUSED
#: rather than quietly filed as a key, or an unmigrated subscriber registers
#: cleanly and never fires — the silence the arrow was adopted to remove.
_RETIRED_PREFIX = "edge:"


def is_retired_point(text: str) -> bool:
    """Whether ``text`` is the retired ``edge:<from>--<to>`` spelling.

    Narrower than ``startswith("edge:")`` on purpose: ``edge:<name>`` with no
    pair is the LIVE alias key a declared edge name mints (``name: reclaim``),
    and refusing that breaks a working feature (HATS-1719 review).
    """
    if not text.startswith(_RETIRED_PREFIX):
        return False
    source, sep, target = text[len(_RETIRED_PREFIX) :].partition("--")
    return bool(sep and source and target)


def is_event_key(text: str) -> bool:
    """Whether ``text`` addresses a NON-FSM event rather than denoting an arrow.

    Those keys carry a namespace (``link:``, ``read:``, ``op:``, the retired
    ``edge:``), and their tail is user-authored — a link kind may legally be
    called ``a->b``. Sniffing for an arrow without asking this first turned such
    a backlog into a crash where it used to work (HATS-1719 review).
    """
    return ":" in text.split(ARROW, 1)[0]


def parse_selector(text: str) -> Selector | None:
    """The arrow this text derives, or ``None`` when it holds no arrow at all.

    ``None`` is not an error: the checks DSL is shared by several applications,
    and a name without an arrow is simply addressed elsewhere. A malformed
    ARROW is not this function's business either — it derives, and
    :func:`selector_form` judges.
    """
    if ARROW not in text or is_event_key(text):
        return None
    source, _, target = text.partition(ARROW)
    return Selector(source or ANY, target or ANY)


def selector_form(text: str) -> str | None:
    """Why ``text`` is not a legal selector, or ``None`` when it is.

    The predicate answers from the STRING, never from a topology: whether a real
    edge is named is a question only a holder of the topology can answer, and it
    is answered elsewhere (``dead_point_reason``). What can be answered here is
    whether the name is in the grammar at all — and it must be, because the
    declaration is a security boundary: a misspelt protected row disarmed both
    roads into master and no channel said a word (HATS-1682 A5).

    A form this slice does not enable is refused BY NAME of the card that opens
    it, so the spelling cannot be taken by someone else in the meantime.
    """
    if any(ch.isspace() for ch in text):
        return (
            f"a selector carries no whitespace: {text!r} would be a second spelling of one "
            f"selector, and the dedup key holds it verbatim — write "
            f"{''.join(text.split())!r}"
        )
    if ARROW not in text:
        return (
            f"a rack selector is an arrow — {ARROW!r} between the state names of the backlog "
            f"it gates, as in 'review->done' (exactly this edge) or '->done' (any road in)"
        )
    if text.count(ARROW) != 1:
        return f"a selector carries exactly one {ARROW!r}, and {text!r} carries {text.count(ARROW)}"
    source, _, target = text.partition(ARROW)
    if not source and not target:
        return (
            f"{text!r} leaves both halves empty, which is a typo rather than 'everywhere' — "
            f"'everywhere' is spelled with the word"
        )
    if NONE in (source, target):
        return (
            f"{NONE!r} is reserved and not yet enabled: card creation as an event is HATS-1703, "
            f"and destruction has no call site at all"
        )
    if ANY in (source, target) and not source == target == ANY:
        # ``ANY`` earns its keystrokes only as "everywhere"; on ONE side it is a
        # second spelling of the empty side, and ``AppBinding.identity`` holds the
        # selector verbatim — so two spellings of one selector are two rows.
        other = target if source == ANY else source
        instead = (
            f"{ANY}{ARROW}{ANY}"
            if not other
            else f"{ARROW}{other}"
            if source == ANY
            else f"{other}{ARROW}"
        )
        return (
            f"{ANY!r} spells 'everywhere', and only on BOTH sides: {text!r} is a second "
            f"spelling of {instead!r} — write that one"
        )
    for half, end in (("source", source), ("target", target)):
        if not end:
            continue
        stray = sorted({ch for ch in end if ch not in _STATE_CHARS})
        if stray:
            return (
                f"the {half} of {text!r} is not a state name: it carries {stray} — "
                f"a selector is ONE arrow between two names, so 'a{ARROW}{ARROW}b' and "
                f"an invisible character before the arrow are typos, not selectors"
            )
        if end.endswith("-"):
            return (
                f"the {half} of {text!r} ends in '-', which is how '--{'>'}' mistypes as an "
                f"arrow; a selector carries exactly one '{ARROW}'"
            )
    return None


def is_wide_output(text: str) -> bool:
    """Whether ``text`` leaves the TARGET open — "every way out of a state".

    The one predicate behind both vetoes below (design.md §10.3). ``ANY->ANY`` is
    a wide output too: everywhere includes every way out.
    """
    parsed = parse_selector(text)
    return parsed is not None and parsed.target == ANY


def gate_veto(text: str) -> str | None:
    """Why a row that can REFUSE may not stand on ``text``, or ``None`` when it may.

    The veto is cut by what the row can DO, not by the selector alone (design.md
    §10.3). Every declared ``run:`` row today runs in-lock and may refuse, and one
    refusal on a wide output stands on EVERY way out of the state — the ways that
    abandon the card included — so the card is locked where it is for good.
    Measured, because the obvious escapes are not ones: ``on_error: warn`` softens
    a check that BROKE and never one that refused (``hook_exec.HookRun.downgradable``),
    and ``--force`` relaxes the FSM arrow while the check still runs.
    """  # comment-length: allow — which half of the rule is measured is the decision
    parsed = parse_selector(text)
    if parsed is None or parsed.target != ANY:
        return None
    leaving = "any state" if parsed.source == ANY else repr(parsed.source)
    return (
        f"{text!r} is a wide OUTPUT, and a row that can refuse may not stand on one: it "
        f"would run on every way out of {leaving} — the ways that abandon the card "
        f"included — and a single refusal locks the card there for good ('on_error: warn' "
        f"softens a check that broke, never one that refused; '--force' relaxes the FSM "
        f"arrow, not the check). Bind it to the roads IN ('{ARROW}<state>') or to an exact "
        f"arrow; a row that only NOTIFIES becomes legal here with HATS-1723"
    )
