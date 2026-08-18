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

from dataclasses import dataclass

#: Reserved. Upper-case because state names in every shipped topology are
#: lower-case, so the case alone makes the word not a name.
ANY = "ANY"
NONE = "NONE"

ARROW = "->"


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


def parse_selector(text: str) -> Selector | None:
    """The arrow this text derives, or ``None`` when it holds no arrow at all.

    ``None`` is not an error: the checks DSL is shared by several applications,
    and a name without an arrow is simply addressed elsewhere. A malformed
    ARROW is not this function's business either — it derives, and
    :func:`selector_form` judges.
    """
    if ARROW not in text:
        return None
    source, _, target = text.partition(ARROW)
    return Selector(source or ANY, target or ANY)


def selector_form(text: str) -> str | None:
    """Why ``text`` is not a legal selector, or ``None`` when it is.

    The predicate answers from the STRING, never from a topology: whether a real
    edge is named is a question only a holder of the topology can answer, and it
    is answered elsewhere (``dead_point_reason``). What can be answered here is
    whether the name is in the grammar at all — and it must be, because the
    declaration is a security boundary: a misspelt consent row disarmed both
    roads into master and no channel said a word (HATS-1682 A5).

    A form this slice does not enable is refused BY NAME of the card that opens
    it, so the spelling cannot be taken by someone else in the meantime.
    """
    if any(ch.isspace() for ch in text):
        return (
            f"a selector carries no whitespace: {text!r} would be a second spelling of one "
            f"selector, and the dedup key holds it verbatim — write {text.replace(' ', '')!r}"
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
    if target in ("", ANY):
        return (
            f"a wide OUTPUT ({text!r}) is HATS-1720: it needs the veto rule that comes with it, "
            f"because a gate able to refuse would lock the card in that state on every way out"
        )
    if source == ANY:
        return f"{ANY!r} spells 'everywhere' on BOTH sides; for any road into a state write '{ARROW}{target}'"
    return None
