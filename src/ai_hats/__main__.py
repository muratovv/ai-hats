"""``python -m ai_hats`` — the sole package entry point.

HATS-790 (Alt 5): the ``[project.scripts] ai-hats`` console-script generator
was removed, so no venv materialises a ``bin/ai-hats`` proxy that direnv could
shadow ahead of the host launcher. The bash launcher and
:func:`ai_hats._bootstrap.bootstrap_or_die` both re-exec via
``<venv>/bin/python -m ai_hats``, which lands here.

Routes through :func:`ai_hats.cli.main_entry` (NOT the bare ``main`` click
group) so ``--tree`` / ``--tree <path>`` / ``--help --tree`` ordering behaves
identically to the old console-script entry point — ``main_entry`` is the only
thing that intercepts ``--tree`` before click's eager-flag parsing.
"""

from __future__ import annotations

from .cli import main_entry


if __name__ == "__main__":
    main_entry()
