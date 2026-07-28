"""``python -m ai_hats_rack`` — interpreter-tier entry point (HATS-1263).

The ``rack`` console script materialises only in a built venv; the e2e shim
tier drives interpreters directly and needs this fallback.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
