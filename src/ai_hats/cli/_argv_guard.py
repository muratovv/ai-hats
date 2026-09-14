"""Refuse an ai-hats word or flag that would otherwise reach the provider.

Bare `ai-hats` forwards positionals to the provider (`_PassthroughGroup`)
— right for prose, wrong for anything ai-hats owns: five sessions were
recorded where `ai-hats githooks --help` reached `claude` as a prompt,
printed claude's usage and was SIGTERM'd 6 s later.
Ownership is read off the live group, never listed here: a word is real when it
is mounted, a flag is ai-hats' own when the group declares it. Pure by
construction — no click, no I/O; the caller supplies both sets.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: click injects `--help` at parse time rather than declaring it, and `-h` is the
#: spelling users reach for; every other owned flag is read off the group.
_UNDECLARED_HELP = frozenset({"--help", "-h"})

# Detail, not the safety net: a lone unknown word is refused whether or not it is
# listed here. An entry buys the reader the spelling that replaced it, and covers
# the multi-token invocation (`ai-hats task list`) a lone word cannot.
RESERVED: Mapping[str, str] = {
    "githooks": "It is not a CLI surface at all — installed git hooks call the module:\n"
    "    python -m ai_hats.cli.githooks_hook",
    "task": "The backlog moved to the `rack` CLI:\n    rack ls",
    "hyp": "Hypotheses moved to the `rack` CLI:\n    rack ls --backlog hyp",
    "proposal": "Proposals moved to the `rack` CLI:\n    rack ls --backlog proposal",
    "attach": "Attachments moved to the `rack` CLI:\n    rack transition --attach",
    "run": "It was renamed:\n    ai-hats agent",
    "bump": "It was removed — `ai-hats self update` runs the bump internally.",
    "rollback": "It was removed — recover with `git checkout`.",
    "sync-hooks": "It was removed — hook drift heals at session start.",
    "migrate": "It was removed — migration runs inline in `ai-hats self update`.",
    "migrate-v07": "It was removed — migration runs inline in `ai-hats self update`.",
}


def _two_columns(rows: Sequence[tuple[str, str]]) -> str:
    """Align the way-out table: the token is user-supplied, so the width varies."""
    width = max(len(left) for left, _ in rows) + 4
    return "\n".join(f"  {left:<{width}}{right}" for left, right in rows)


def own_flags(group: object) -> frozenset[str]:
    """Every flag ai-hats itself declares on its root group.

    The mirror of :func:`command_paths`: a word is real when it is mounted, a flag
    is ai-hats' own when the group declares it. Unknown flags stay the provider's,
    which is what the passthrough surface promises.
    """
    flags = set(_UNDECLARED_HELP)
    for param in getattr(group, "params", ()):
        flags.update(getattr(param, "opts", ()))
        flags.update(getattr(param, "secondary_opts", ()))
    return frozenset(flags)


def command_paths(group: object) -> dict[str, str]:
    """Map every command name in the tree to its full path (``"init"`` → ``"self init"``).

    Breadth-first so a shallower name always wins: `list` is the top-level group,
    never `session list`. Duck-typed on ``.commands`` — the click group is the
    only caller, but the walk needs no click import to be tested.
    """
    paths: dict[str, str] = {}
    frontier: list[tuple[object, tuple[str, ...]]] = [(group, ())]
    while frontier:
        nxt: list[tuple[object, tuple[str, ...]]] = []
        for cmd, prefix in frontier:
            for name, sub in getattr(cmd, "commands", {}).items():
                paths.setdefault(name, " ".join((*prefix, name)))
                nxt.append((sub, (*prefix, name)))
        frontier = nxt
    return paths


def classify(
    raw_argv: Sequence[str],
    leftover: Sequence[str],
    known_paths: Mapping[str, str],
    known_flags: frozenset[str] = frozenset(),
) -> str | None:
    """The refusal for this invocation, or ``None`` to let it through.

    ``raw_argv`` is argv as typed — the only place ``--`` survives, since click
    consumes the separator while parsing. ``leftover`` is what click left for the
    provider. ``known_paths`` maps each command name to its full path
    (``"init" -> "self init"``) and ``known_flags`` is what ai-hats declares for
    itself — both read off the live group rather than written out here.
    """
    if "--" in raw_argv:
        return None  # passthrough asked for explicitly
    if not leftover:
        return None  # bare `ai-hats` — the primary surface

    first = leftover[0]
    ways_out = _two_columns(
        [
            ("ai-hats --help", "list the real subcommands"),
            (f"ai-hats -- {first}", f"send `{first}` to the provider as a prompt"),
        ]
    )

    if first in RESERVED:
        return f"`{first}` is not an ai-hats subcommand. {RESERVED[first]}\n\n{ways_out}"

    path = known_paths.get(first)
    if path == first:
        return None  # a live top-level command — click's own routing owns it
    if path:
        return (
            f"`{first}` is not a top-level ai-hats command — did you mean:\n"
            f"    ai-hats {path}\n\n{ways_out}"
        )

    flag = next((a for a in leftover if a in known_flags), None)
    if flag:
        table = _two_columns(
            [
                (f"ai-hats {flag} ...", "ai-hats' own flags go before the prompt"),
                (f"ai-hats -- {flag}", f"forward `{flag}` to the provider"),
            ]
        )
        return (
            f"`{flag}` is an ai-hats flag, but here it would reach the provider instead.\n\n{table}"
        )

    # Nothing above resolved, so a LONE bare word is a command the user expected
    # to exist. Being mounted is what makes a name real, so refuse by default and
    # let RESERVED add detail — rather than leaning on RESERVED to stay complete.
    if len(leftover) == 1 and not first.startswith("-") and " " not in first:
        return f"`{first}` is not an ai-hats command.\n\n{ways_out}"

    return None
