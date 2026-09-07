#!/usr/bin/env python3
"""Check every http(s) link in a text file; exit 1 only on a dead one.

Usage: check_links.py <file> [<file> ...]

One line per link: ``OK``, ``DEAD <status>`` (404/410), ``WARN <status>``
(any other non-2xx/3xx — a bot filter's 403 is not a dead link), or
``UNREACHABLE <reason>`` (network, DNS, timeout — not the link's fault).
Exit 1 iff at least one link is DEAD; UNREACHABLE and WARN never fail the run.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEAD_STATUSES = {404, 410}
TIMEOUT_S = 10
URL_RE = re.compile(r"https?://[^\s<>()\"'`\]]+")
TRAILING_PUNCT = ".,;:!?"

# Some hosts answer a bare Python UA with 403; a browser-like UA is the norm for link checkers.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; check_links/1.0)"}


def extract_urls(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(TRAILING_PUNCT)
        seen.setdefault(url, None)
    return list(seen)


def probe(url: str) -> tuple[str, str]:
    """Return (verdict, detail) for one URL — verdict in OK/DEAD/WARN/UNREACHABLE."""
    for method in ("HEAD", "GET"):
        # URL_RE admits only http(s), so the scheme audit does not apply.
        req = urllib.request.Request(url, method=method, headers=HEADERS)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
                return "OK", str(resp.status)
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (405, 501):
                continue  # server refuses HEAD; ask again with GET
            if exc.code in DEAD_STATUSES:
                return "DEAD", str(exc.code)
            return "WARN", str(exc.code)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            return "UNREACHABLE", str(reason)
    return "WARN", "HEAD and GET both refused"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    dead = 0
    total = 0
    for name in argv:
        text = Path(name).read_text(encoding="utf-8")
        for url in extract_urls(text):
            total += 1
            verdict, detail = probe(url)
            if verdict == "DEAD":
                dead += 1
            print(f"{verdict} {detail} {url}")
    print(f"checked {total} link(s), dead {dead}")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
