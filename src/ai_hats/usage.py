"""Back-compat shim: ``usage/v1`` moved to ``ai_hats_observe.usage`` (HATS-953, T15/0.3.0).

Keeps ``from ai_hats.usage import parse_session_usage`` and ``python -m
ai_hats.usage <jsonl>`` working for historical-sweep callers; the canonical home
is ``ai_hats_observe.usage``.
"""

from __future__ import annotations

from ai_hats_observe.usage import SCHEMA_VERSION, _main, parse_session_usage

__all__ = ["SCHEMA_VERSION", "parse_session_usage"]


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
