"""``python -m ai_hats`` — the sole package entry point (HATS-790, HATS-1120)."""

from __future__ import annotations

from .constants import is_debug_mode


def main() -> None:
    # The gate runs BEFORE `.cli` is imported. Its subcommand package
    # imports workspace members at module level, so a venv missing one of them
    # never reaches the in-CLI gate — it dies importing the module that holds it.
    from ._bootstrap import bootstrap_or_die

    bootstrap_or_die()

    try:
        from .cli import main_entry

        main_entry()
    except Exception as exc:
        from .self_heal import is_broken_install_exception

        if is_debug_mode() or not is_broken_install_exception(exc):
            raise
        try:
            from .cli._helpers import _handle_broken_install_or_die

            _handle_broken_install_or_die(exc)
        except Exception:
            # Last resort: cli._helpers is itself unimportable, so the notice is
            # rendered from _bootstrap — stdlib-only, and thus always available.
            from ._bootstrap import repair_command
            from .startup_notices import show_fatal_notice_and_exit

            show_fatal_notice_and_exit(
                f"Inconsistent or broken ai-hats installation ({exc}).\n"
                "Likely cause: package files are out of sync or corrupted.\n"
                f"Repair command: {repair_command()}\n"
                "Debug with: AI_HATS_DEBUG=1, AI_HATS_VERBOSE=1, --debug, --verbose, -v"
            )


if __name__ == "__main__":
    main()
