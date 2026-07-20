"""CLI entry for ``python -m ai_hats_rack.migration``."""

import sys

from .core import main

if __name__ == "__main__":
    sys.exit(main())
