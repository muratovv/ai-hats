"""The argv of the grok process an ACP holder spawns."""

from __future__ import annotations

_MODEL = {"--model", "-m"}
# Flags the one-shot launch uses. This child is a session, so none of them belong.
_DROP_WITH_VALUE = {"--session-id"}
_DROP = {"--always-approve", "-p", "--print"}


def grok_acp_argv(planned: list[str]) -> list[str]:
    """`grok agent --no-leader stdio`, plus a model override when the plan carried one.

    Everything else on the planned argv is a TUI or one-shot flag. `--session-id`
    in particular is not a flag of `grok agent`, and the session id arrives later
    from `session/new`.
    """
    binary = planned[0] if planned else "grok"
    model: str | None = None
    args = planned[1:]
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _MODEL and index + 1 < len(args):
            model = args[index + 1]
            index += 2
            continue
        if arg.startswith("--model="):
            model = arg.split("=", 1)[1]
            index += 1
            continue
        if arg in _DROP_WITH_VALUE and index + 1 < len(args):
            index += 2
            continue
        if arg in _DROP or arg in _DROP_WITH_VALUE:
            index += 1
            continue
        index += 1
    argv = [binary, "agent", "--no-leader", "stdio"]
    if model:
        argv.extend(["--model", model])
    return argv
