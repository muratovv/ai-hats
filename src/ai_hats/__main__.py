"""Allow ``python -m ai_hats`` as a fallback to the ``ai-hats`` console script.

Used by :func:`ai_hats._bootstrap.bootstrap_or_die` to re-exec into a fresh
interpreter after self-healing missing runtime deps.
"""

from __future__ import annotations

from .cli import main


if __name__ == "__main__":
    main()
