"""HATS-939: shipped git hooks must run on macOS system bash 3.2, not only 4+.

`env bash` resolves to /bin/bash 3.2 when a hook's PATH lacks a Homebrew bash
(e.g. inside `ai-hats wt exec`); a bash-4-only builtin then aborts the commit.
This guard fails RED if any `library/**/git_hooks/*.sh` reintroduces one.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
GIT_HOOKS = sorted(REPO_ROOT.glob("packages/ai-hats-library/src/ai_hats_library/**/git_hooks/*.sh"))

# bash-4-only constructs, anchored to command position (`^\s*`) so a prose
# comment that merely names one does not trip the guard.
BASH4_ONLY = [
    ("mapfile/readarray", re.compile(r"^\s*(mapfile|readarray)\b")),
    (
        "declare/local/typeset -A (associative array)",
        re.compile(r"^\s*(declare|local|typeset)\s+-[A-Za-z]*A[A-Za-z]*\b"),
    ),
]


def test_no_bash4_only_builtins_in_shipped_git_hooks():
    # Guard against a silent vacuous pass if the glob or layout drifts.
    assert GIT_HOOKS, "no library/**/git_hooks/*.sh found — glob/path drift"

    offenders: list[str] = []
    for hook in GIT_HOOKS:
        for lineno, raw in enumerate(hook.read_text().splitlines(), 1):
            if raw.lstrip().startswith("#"):
                continue
            for label, pat in BASH4_ONLY:
                if pat.search(raw):
                    offenders.append(f"{hook.name}:{lineno}: {label} — {raw.strip()}")

    assert not offenders, (
        "bash-4-only builtins break macOS system bash 3.2 (HATS-939):\n"
        + "\n".join(offenders)
    )
