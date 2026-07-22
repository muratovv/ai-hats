"""``python -m ai_hats`` — the sole package entry point (HATS-790, HATS-1120)."""

from __future__ import annotations

import sys
from .constants import is_debug_mode


def main() -> None:
    try:
        from .cli import main_entry

        main_entry()
    except (ImportError, AttributeError) as exc:
        if is_debug_mode():
            raise
        try:
            from .cli._helpers import _handle_broken_install_or_die

            _handle_broken_install_or_die(exc)
        except Exception:
            sys.stderr.write(
                f"Error: Inconsistent or broken ai-hats installation ({exc}).\n"
                "Likely cause: package files are out of sync or corrupted.\n"
                "Repair command: python -m ai_hats self update (or 'ai-hats self update')\n"
                "Debug with: AI_HATS_DEBUG=1, AI_HATS_VERBOSE=1, --debug, --verbose, -v\n"
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
