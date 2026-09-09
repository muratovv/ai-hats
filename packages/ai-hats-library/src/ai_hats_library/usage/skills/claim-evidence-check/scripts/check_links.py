#!/usr/bin/env python3
"""Check every http(s) link in a text file; exit 1 only on a dead one.

Usage: check_links.py <file> [<file> ...]

Per link: OK · DEAD <status> (404/410) · WARN <status> (other non-2xx/3xx —
a bot filter's 403 is not a dead link) · UNREACHABLE (network) · MALFORMED
(urllib refuses the URL before any request).
Exit 1 iff a link is DEAD, 2 on bad input. Every other verdict keeps the run
going, so one bad link never hides the ones after it.
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


def _attempt(url: str, method: str) -> tuple[str, str] | None:
    """One request. None means the server refused the METHOD itself — retry-worthy."""
    try:
        # URL_RE admits only http(s), so the scheme audit does not apply.
        req = urllib.request.Request(url, method=method, headers=HEADERS)  # noqa: S310
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
            return "OK", str(resp.status)
    except ValueError as exc:
        # urllib rejects a malformed URL while building the request — before any
        # socket. Uncaught, it aborted the whole run on one typo.
        return "MALFORMED", str(exc)
    except urllib.error.HTTPError as exc:
        if exc.code in (405, 501):
            return None
        if exc.code in DEAD_STATUSES:
            return "DEAD", str(exc.code)
        return "WARN", str(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return "UNREACHABLE", str(reason)


def probe(url: str) -> tuple[str, str]:
    """Return (verdict, detail) — OK/DEAD/WARN/UNREACHABLE/MALFORMED."""
    verdict = _attempt(url, "HEAD") or _attempt(url, "GET")
    return verdict or ("WARN", "HEAD and GET both refused")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    dead = 0
    malformed = 0
    total = 0
    for name in argv:
        try:
            text = Path(name).read_text(encoding="utf-8")
        except OSError as exc:
            # Exit 2 is the input's fault; 1 is reserved for a dead link.
            print(f"cannot read {name}: {exc}", file=sys.stderr)
            return 2
        for url in extract_urls(text):
            total += 1
            verdict, detail = probe(url)
            if verdict == "DEAD":
                dead += 1
            elif verdict == "MALFORMED":
                malformed += 1
            print(f"{verdict} {detail} {url}")
    print(f"checked {total} link(s), dead {dead}, malformed {malformed}")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
