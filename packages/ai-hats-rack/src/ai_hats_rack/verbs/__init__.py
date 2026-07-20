"""``verbs/`` — one module per verb family, each a ``def verb()`` builder that
yields a :class:`Verb` (HATS-1036 R4, ADR-0017 §7).

``cli.py`` is the aggregator: ``init_verbs`` finalizes each verb against the
backlog definition via :func:`on_user_schema` and registers it on ``main``. A
schema-driven verb (create) generates its options from ``fields[]``; the others
ignore the definition and return their default command. The package holds NO
first-party imports (import-hygiene pin) and never imports ``cli`` (no cycle) —
shared wiring lives in ``cli_kernel``/``cli_common``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import click

from ..definition import BacklogDefinition


@dataclass(frozen=True)
class Verb:
    """A CLI verb: its name + a factory that finalizes its command against a
    backlog definition (schema-driven verbs read ``fields[]``/``edges``; others
    ignore ``defn`` and return their default command)."""

    name: str
    factory: Callable[[BacklogDefinition], click.Command]


def on_user_schema(verb: Verb, defn: BacklogDefinition) -> click.Command:
    """Finalize a verb's command from the backlog definition (R4/ADR-0017 §7)."""
    return verb.factory(defn)


def all_verbs() -> list[Verb]:
    """The top-level verbs (create / transition / context / ls / plan-extract).
    Submodules are imported lazily so ``__init__`` carries no submodule cycle."""
    from .create import verb as create_verb
    from .plan_extract import verb as plan_extract_verb
    from .read import context_verb, ls_verb
    from .transition import verb as transition_verb

    return [
        create_verb(),
        transition_verb(),
        context_verb(),
        ls_verb(),
        plan_extract_verb(),
    ]


__all__ = ["Verb", "all_verbs", "on_user_schema"]
