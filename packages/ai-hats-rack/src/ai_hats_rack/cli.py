"""``rack`` — minimal JSON-first CLI over the bare kernel (HATS-1020).

The top-level verbs (create / transition / context / ls / plan-extract) live in
the ``verbs/`` package (HATS-1036); this module is the aggregator — builds ``main`` from the
packaged tasks definition via ``init_verbs`` and re-exports the shared wiring /
renderers under their historical ``cli._*`` names so the dotted-path pins
(test_cli_wiring / test_error_surface) resolve unchanged. Entry point
``ai_hats_rack.cli:main`` is UNTOUCHED.
"""

from __future__ import annotations

import click

from .definition import load_backlog
from .verbs import Verb, all_verbs, on_user_schema
from .cli_root import root_group
from .verbs.groups import RackGroup

# Re-exported for the dotted-path pins (test_cli_wiring / test_error_surface) and
# for consumers that read the wiring seam through ``cli.*``.
from .cli_kernel import (  # noqa: F401  (re-export)
    KERNEL_FACTORY_GROUP,
    KernelProvider,
    _bare_kernel,
    _build_kernel,
    _echo_deltas,
    _provider,
    _result_payload,
    _workspace,
)
from .verbs.transition import _OP_RENDERERS  # noqa: F401 — pinned exhaustive over ops.OP_KINDS


@click.group(cls=RackGroup)
def main() -> None:
    """rack — minimal backlog kernel CLI (ai-hats-rack)."""


def init_verbs(verbs: list[Verb], defn) -> None:
    """Finalize each verb against the backlog definition (schema-driven create
    generates its options from ``fields[]``) and register it on ``main``."""
    for v in verbs:
        main.add_command(on_user_schema(v, defn))


# The top-level surface is the tasks backlog, built from the packaged definition;
# per-backlog groups (hyp/proposal) are added lazily by RackGroup when the ambient
# workspace mounts sibling catalogs (HATS-1036 R2) — nothing here until then.
init_verbs(all_verbs(), load_backlog())

# The cross-project roots registry group (HATS-1081) — static, not backlog-scoped.
main.add_command(root_group)


if __name__ == "__main__":
    main()
