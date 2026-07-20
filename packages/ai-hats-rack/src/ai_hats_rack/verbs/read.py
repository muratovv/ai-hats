"""``rack context`` / ``rack ls`` — the read surface (HATS-1036 R4).

The command bodies + rendering helpers stay in ``cli_context`` (one home for the
read renderers); these are the thin ``verb()`` builders that register them. Both
are schema-blind — they ignore ``defn`` and return their static command.
"""

from __future__ import annotations

from ..cli_context import context_cmd, ls_cmd
from . import Verb


def context_verb() -> Verb:
    return Verb("context", lambda defn: context_cmd)


def ls_verb() -> Verb:
    return Verb("ls", lambda defn: ls_cmd)
