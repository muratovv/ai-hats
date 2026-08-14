#!/usr/bin/env python3
"""Bidirectional control characters are invisible in review — refuse them.

HATS-1591. A Trojan Source attack reorders how source is DISPLAYED without
changing how it is PARSED, so a reviewer and a compiler read two different
programs. The nine characters below are the whole mechanism.

This is the one applicable check bandit held that ruff's `S` family does not
(`B613 trojansource`; the other three bandit-only checks name django, pytorch
and huggingface, none of which this repo depends on). Keeping bandit for it
would have cost a second pin and a second suppression dialect on every
by-design site — this file is the cheaper half of that trade.
"""  # comment-length: allow — the contract is why the file exists

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The Trojan Source set: explicit embeddings/overrides (U+202A..U+202E) and
# isolates (U+2066..U+2069). Written as escapes on purpose — a literal here
# would be a finding in the file that reports them.
BIDI = {
    "\u202a": "LEFT-TO-RIGHT EMBEDDING",
    "\u202b": "RIGHT-TO-LEFT EMBEDDING",
    "\u202c": "POP DIRECTIONAL FORMATTING",
    "\u202d": "LEFT-TO-RIGHT OVERRIDE",
    "\u202e": "RIGHT-TO-LEFT OVERRIDE",
    "\u2066": "LEFT-TO-RIGHT ISOLATE",
    "\u2067": "RIGHT-TO-LEFT ISOLATE",
    "\u2068": "FIRST STRONG ISOLATE",
    "\u2069": "POP DIRECTIONAL ISOLATE",
}


def tracked_files(root: Path) -> list[Path]:
    """Every file git tracks, so build output and stray scratch never answer."""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [root / name for name in out.stdout.split("\0") if name]


def findings(paths: list[Path]) -> list[str]:
    found = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable — no source to hide anything in
        if not any(ch in text for ch in BIDI):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for ch, name in BIDI.items():
                col = line.find(ch)
                if col >= 0:
                    found.append(f"{path}:{lineno}:{col + 1}: U+{ord(ch):04X} {name}")
    return found


def main(argv: list[str]) -> int:
    try:
        paths = [Path(a) for a in argv] if argv else tracked_files(REPO_ROOT)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"[bidi] could not enumerate files: {exc}", file=sys.stderr)
        return 2

    found = findings(paths)
    for violation in found:
        print(f"[bidi] FAIL: {violation}", file=sys.stderr)
    if found:
        print(
            "[bidi] These characters change how the line RENDERS, not how it "
            "parses — a reviewer cannot see them. Delete them; if one is truly "
            "required (a test fixture for this very attack), build it at "
            "runtime from an escape instead of storing it in the tree.",
            file=sys.stderr,
        )
        return 1
    print(f"[bidi] ok: no bidirectional controls in {len(paths)} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
