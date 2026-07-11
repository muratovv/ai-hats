"""``ai-hats-tracker`` console entry — the standalone tracker CLI (ADR-0016).

ADR-0016 makes ``ai-hats-tracker`` a pure engine that a portable skill DECLARES
as a ``requires.cli`` dependency. This root group is the ``[project.scripts]``
console entry so ``ai-hats-tracker --version`` — the ``backlog-manager`` skill's
presence probe — resolves on ``PATH`` after ``pip install ai-hats-tracker``. It
mounts the same wt-free ``task``/``attach``/``hyp``/``proposal`` groups the
integrator overlays with its wt-wired ``_seam`` at mount time.
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


main.add_command(task)
main.add_command(attach)
main.add_command(hyp)
main.add_command(proposal)


if __name__ == "__main__":
    main()
