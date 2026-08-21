#!/usr/bin/env python3
"""HATS-1754 — the spellings a guarded binary reaches the shell under.

The guard used to know one: the console script. `WRAPPERS` covered `sudo` and
`timeout`, so `timeout 60 ai-hats wt merge` raised the question — while
`uv run ai-hats wt merge` and `python3 -m ai_hats_rack transition … execute`
returned NOTHING at all (measured 2026-08-20). Both roads into master and the
`plan -> execute` arrow were reachable by re-spelling the command, in silence.

The table lives beside the gate rather than inside it because the permission
lint probes what the gate can see: kept in two places, the two drift, and a
spelling the guard watches while the lint stays quiet is the exact failure the
lint exists to report.
"""

from __future__ import annotations

import os
import re

#: Binaries that run another binary the way `timeout` does — the guarded call is
#: an operand, at an offset nobody should try to count (HATS-1682).
RUNNERS = ("uv", "uvx")

#: What `<interpreter> -m <module>` actually launches. Only modules that RUN:
#: `python -m ai_hats.cli` exits 1 ("package and cannot be directly executed",
#: measured), and probing it would report a hole no one can walk through.
MODULE_BINARIES = {
    "ai_hats": "ai-hats",
    "ai_hats_rack": "rack",
    "ai_hats_rack.cli": "rack",
    "ai_hats_library.hooks.consent_gate.cli": "consent",
}

#: `python`, `python3`, `python3.11` — and any of them behind a path.
_INTERPRETER = re.compile(r"python\d*(?:\.\d+)?")

#: The interpreters a spelling is WRITTEN with. Narrower than what
#: :func:`is_interpreter` recognises on the way in: a probe list has to be
#: finite, while the guard should read any interpreter it meets.
INTERPRETERS = ("python", "python3", ".venv/bin/python")


#: Binaries that run whatever the caller writes. A rule handing one of these out
#: cannot be bounded by a probe: the command line does not name the operation —
#: `python -c "subprocess.run([...])"` is a different STRING that does the guarded
#: thing, and reaching it is analysis over arbitrary code, not prefix matching.
SHELLS = ("sh", "bash", "zsh", "fish", "dash", "ksh", "source", "node", "ruby", "perl")


def is_interpreter(token: str) -> bool:
    """True when ``token`` names a Python interpreter, path spellings included."""
    return bool(_INTERPRETER.fullmatch(os.path.basename(token)))


def module_binary(tokens):
    """``[binary, *args]`` for a `<interpreter> -m <module>` call, else ``[]``.

    Returns the call as the guard's checks expect to read it — binary first,
    its own arguments after — so `merge_branch` and `transition_target` need to
    know nothing about interpreters.
    """
    if not tokens or not is_interpreter(tokens[0]):
        return []
    try:
        at = tokens.index("-m")
    except ValueError:
        return []
    if at + 1 >= len(tokens):
        return []
    binary = MODULE_BINARIES.get(tokens[at + 1])
    return [binary, *tokens[at + 2 :]] if binary else []


def spellings_for(command: str) -> list[str]:
    """Every way ``command`` can reach the shell, the console script first.

    The permission lint probes this list, and the guard sees the same shapes —
    that is the point of one table: a spelling the guard watches while the lint
    stays silent is the hole the lint exists to report.
    """
    binary, _, rest = command.partition(" ")
    out = [command, *(f"{runner} {command}" for runner in RUNNERS)]
    for module, mapped in MODULE_BINARIES.items():
        if mapped != binary:
            continue
        for interpreter in INTERPRETERS:
            call = f"{interpreter} -m {module} {rest}".rstrip()
            out.append(call)
            out.append(f"uv run {call}")
    return out


def is_interpreter_or_shell(token: str) -> bool:
    """True when ``token`` names something that runs code the caller supplies."""
    return is_interpreter(token) or os.path.basename(token) in SHELLS
