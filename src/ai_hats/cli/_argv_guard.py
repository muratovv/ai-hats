"""Refuse an ai-hats word that would otherwise be launched as a provider prompt.

Bare `ai-hats` forwards positional text to the provider (`_PassthroughGroup`,
HATS-1202) — right for prose, wrong for a mistyped command: HATS-1932 recorded
five sessions where `ai-hats githooks --help` reached `claude` as a prompt,
printed claude's usage and was SIGTERM'd 6 s later, leaving a dead session
directory instead of an error.

Pure by construction — no click, no I/O; the caller supplies the command tree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: Never a prompt — always a user reaching for ai-hats' own help.
HELP_FLAGS = frozenset({"--help", "-h", "--version"})

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
) -> str | None:
    """The refusal for this invocation, or ``None`` to let it through.

    ``raw_argv`` is argv as typed — the only place ``--`` survives, since click
    consumes the separator while parsing. ``leftover`` is what click left for the
    provider. ``known_paths`` maps each command name to its full path
    (``"init" -> "self init"``).
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

    flag = next((a for a in leftover if a in HELP_FLAGS), None)
    if flag:
        table = _two_columns(
            [
                ("ai-hats --help", "ai-hats' own help"),
                (f"ai-hats -- {flag}", f"forward `{flag}` to the provider"),
            ]
        )
        return f"`{flag}` would be forwarded to the provider, not handled by ai-hats.\n\n{table}"

    # Nothing above resolved, so a LONE bare word is a command the user expected
    # to exist. Being mounted is what makes a name real, so refuse by default and
    # let RESERVED add detail — rather than leaning on RESERVED to stay complete.
    if len(leftover) == 1 and not first.startswith("-") and " " not in first:
        return f"`{first}` is not an ai-hats command.\n\n{ways_out}"

    return None
