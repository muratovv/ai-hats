"""Base of the rack typed-error hierarchy (HATS-1033).

Every CLI-surfaced domain error descends from :class:`RackError`; the dispatch
table in :mod:`.cli_common` owns one handler per concrete subclass and
``tests/test_error_surface.py`` pins that a new subclass without a reachable
handler fails CI (never a silent bare traceback).
"""

from __future__ import annotations


class RackError(Exception):
    """Base for every rack domain error surfaced through the CLI error table."""


class RackConfigError(RackError):
    """A loaded config file (fsm.yaml / links.yaml / catalog) is malformed.

    Structural invariant, not a user refusal: the CLI error table routes the
    whole subtree to a single ``internal`` marker.
    """
