"""HATS-1816 — the operations consent can be declared on, each knowing itself.

One grammar, read once. Before this module the session wrapper and the PreToolUse
gate each read a guarded verb their own way, and they had already diverged: the
gate skipped flags and their values, the wrapper compared argv positions. Measured
on master 270f51d0, three shapes disagreed — `rack transition --tasks-dir /t X
execute` and `ai-hats --verbose wt merge x` reached the wrapper as no-match while
the gate saw them, and `rack transition --state execute X` matched in the wrapper
with the subject `--state` instead of the task id.

Two earlier fixes in this class shared DATA and left the second reading in place
(HATS-1754 gave both a spellings table, HATS-1781 removed one bypass of it). This
one shares the CODE: a reader that wants a verb calls :func:`read`, and there is
no second place to spell the grammar.
"""

from __future__ import annotations

import string
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Reading:
    """What one invocation of a guarded operation is about."""

    subject: str
    label: str
    #: The state a transition moves TO; ``""`` for operations with no target axis.
    target: str = ""


@dataclass(frozen=True)
class OperationSpec:
    """One operation type, with everything a reader needs to act on it.

    ``surface`` is the binary the session wrapper shims on PATH. ``None`` means
    the operation is guarded by the hook alone and materialises no shim — used
    where wrapping would trap the human, and then ``hook_only_because`` says so
    at the declaration rather than leaving a null to be interpreted.
    """

    type: str
    surface: str | None
    read: Callable[[Sequence[str]], "Reading | None"]
    selector_reason: Callable[[str], str | None]
    admits: Callable[[Sequence[str], str | None, "Reading"], bool]
    legacy_flags: Callable[["Reading"], tuple[str, ...]]
    #: True when the subject identifies a thing a one-shot ticket can bind to.
    binds_ticket: bool = False
    hook_only_because: str = ""
    value_flags: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.surface is None and not self.hook_only_because:
            raise ValueError(f"{self.type}: an operation with no surface must say why")


def operands(argv: Sequence[str], value_flags: frozenset[str]) -> list[str]:
    """The positional tokens of ``argv``: flags dropped, their values with them.

    The single grammar the divergence came from. `--tasks-dir /t` must not leave
    `/t` looking like a task id, and `--verbose` must not push `wt merge` out of
    the positions a reader looks at.
    """
    out: list[str] = []
    skip = False
    for token in argv:
        if skip:
            skip = False
            continue
        if token.startswith("-"):
            skip = token in value_flags
            continue
        out.append(token)
    return out


def flag_value(argv: Sequence[str], flag: str) -> str:
    """The value of ``--flag v`` or ``--flag=v``, or ``""`` when absent."""
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith(f"{flag}="):
            return token.split("=", 1)[1]
    return ""


#: `rack transition` flags that eat the NEXT token. Without this, `--log
#: "execute"` — a note ABOUT the move — reads as the move (HATS-1682).
RACK_VALUE_FLAGS = frozenset(
    {
        "--state", "--attach", "--freeze", "--rm", "--log", "--link", "--unlink",
        "--set", "--append", "--reason", "--resolution", "--final-state", "--tasks-dir",
    }
)  # fmt: skip

_ARROW = "->"


#: What a state name may be spelled with.
_STATE_CHARS = frozenset(string.ascii_letters + string.digits + "_.-")


def _arrow_selector_reason(selector: str) -> str | None:
    """Why ``selector`` is not a usable transition arrow, or ``None`` when it is.

    Wide ends are refused rather than accepted-and-narrowed: `ANY->x` and an
    empty OUTPUT are reserved (HATS-1720), and `NONE` is reserved by HATS-1703.
    """
    if any(character.isspace() for character in selector):
        return "a selector carries no whitespace"
    if _ARROW not in selector:
        return "a rack transition selector is an arrow"
    if selector.count(_ARROW) != 1:
        return f"a selector carries exactly one {_ARROW!r}"
    source, _, target = selector.partition(_ARROW)
    if not source and not target:
        return "both halves empty"
    if "NONE" in (source, target):
        return "'NONE' is reserved by HATS-1703"
    if target in ("", "ANY"):
        return "a wide OUTPUT is reserved by HATS-1720"
    if source == "ANY":
        return f"write '{_ARROW}{target}' instead of 'ANY->{target}'"
    for end in (source, target):
        if {character for character in end if character not in _STATE_CHARS}:
            return "a selector half is not a state name"
        if end.endswith("-"):
            return "a selector half ends in '-'"
    return None


def _pre_merge_only(selector: str) -> str | None:
    return None if selector == "pre-merge" else "supports only 'pre-merge'"


def _read_transition(argv: Sequence[str]) -> Reading | None:
    ops = operands(argv, RACK_VALUE_FLAGS)
    if not ops or ops[0] != "transition":
        return None
    task_id = ops[1] if len(ops) > 1 else ""
    target = flag_value(argv, "--state") or (ops[2] if len(ops) > 2 else "")
    return Reading(subject=task_id, label=f"{task_id}: → {target}", target=target)


def _read_wt_merge(argv: Sequence[str]) -> Reading | None:
    ops = operands(argv, frozenset())
    if ops[:2] != ["wt", "merge"]:
        return None
    # The branch may be omitted — the CLI detects it from the cwd — so this is a
    # LABEL for the question, never the binding. What binds is the invocation.
    branch = ops[2] if len(ops) > 2 else "this worktree"
    return Reading(subject=branch, label=f"merge {branch}")


#: The one answer, per operation, to "does a declared selector cover this call".
def _arrow_admits(selectors, source_state, reading) -> bool:
    return any(
        selector.partition(_ARROW)[2] == reading.target
        and (not selector.partition(_ARROW)[0] or selector.partition(_ARROW)[0] == source_state)
        for selector in selectors
    )


def _pre_merge_admits(selectors, _source_state, _reading) -> bool:
    return "pre-merge" in selectors


_ACK = "AI_HATS_CONSENT_ACK"

REGISTRY: dict[str, OperationSpec] = {
    "rack.transition": OperationSpec(
        type="rack.transition",
        surface="rack",
        read=_read_transition,
        selector_reason=_arrow_selector_reason,
        admits=_arrow_admits,
        # HATS-1743: the older per-move flag is still honoured on the way in.
        legacy_flags=lambda r: (_ACK, "AI_HATS_PLAN_ACK") if r.target == "execute" else (_ACK,),
        binds_ticket=True,
        value_flags=RACK_VALUE_FLAGS,
    ),
    "wt.merge": OperationSpec(
        type="wt.merge",
        surface="ai-hats",
        read=_read_wt_merge,
        selector_reason=_pre_merge_only,
        admits=_pre_merge_admits,
        legacy_flags=lambda _r: (_ACK, "AI_HATS_MERGE_ACK"),
    ),
}


def spec_for(operation: str, *, registry: dict | None = None) -> OperationSpec | None:
    """The specification for ``operation``, or ``None`` when nothing declares it."""
    return (REGISTRY if registry is None else registry).get(operation)


def read(
    operation: str, surface: str, argv: Sequence[str], *, registry: dict | None = None
) -> Reading | None:
    """What ``argv`` on ``surface`` says about ``operation`` — the ONE reading.

    Both the session wrapper and the PreToolUse gate come here. A reader that
    parsed the verb itself would be the second grammar this module exists to
    remove.
    """
    spec = spec_for(operation, registry=registry)
    if spec is None or (spec.surface is not None and spec.surface != surface):
        return None
    return spec.read(argv)


def wrapped_surfaces(operations, *, registry: dict | None = None) -> list[str]:
    """The binaries to shim for ``operations`` — hook-only ones contribute none."""
    found = {
        spec.surface
        for operation in operations
        if (spec := spec_for(operation, registry=registry)) is not None and spec.surface is not None
    }
    return sorted(found)
