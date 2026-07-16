"""``ai-hats-tracker`` console entry — the standalone tracker CLI (ADR-0016).

ADR-0016 makes ``ai-hats-tracker`` a pure engine that a portable skill DECLARES
as a ``requires.cli`` dependency. This root group is the ``[project.scripts]``
console entry so ``ai-hats-tracker --version`` — the ``backlog-manager`` skill's
presence probe — resolves on ``PATH`` after ``pip install ai-hats-tracker``. It
mounts the same wt-free ``task`` group the integrator overlays with its
wt-wired ``_seam`` at mount time, with ``hyp``/``proposal``/``attach`` nested
under ``task`` — the grammar is IDENTICAL to ``ai-hats task …``.
"""

from __future__ import annotations

import click

from .attach import attach
from .hyp import hyp
from .proposal import proposal
from .task import task


@click.group()
@click.version_option(package_name="ai-hats-tracker", prog_name="ai-hats-tracker")
def main() -> None:
    """Standalone task-card FSM + backlog CLI for the ai-hats framework."""


# Same nesting the integrator mounts (ai_hats.cli) — one grammar, two binaries.
task.add_command(hyp)
task.add_command(proposal)
task.add_command(attach)
main.add_command(task)


if __name__ == "__main__":
    main()
