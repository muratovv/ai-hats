"""E2E: shipped library shell scripts stay bash 3.2 compatible (HATS-1355).

macOS ships `/bin/bash` 3.2.57, and two bash-4-only faults have already reached
users: `${var^}` (HATS-1294) and an unguarded empty-array expansion under
`set -u` (HATS-1352). Neither is a syntax error — `bash -n` passes both, and so
does bash 5. This is the wide net over all shipped scripts; the runtime class is
caught by executing hooks under 3.2 (tests/test_pre_commit_smoke_interpreter.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_SRC = REPO_ROOT / "packages" / "ai-hats-library" / "src"

# Constructs bash 3.2 does not have. Each is unambiguous — no flow analysis, so
# no false positives. Anything needing context (the empty-array-under-set-u
# class) is deliberately left to the runtime tests instead of a heuristic.
BASH4_CONSTRUCTS: list[tuple[str, str, str]] = [
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*\^\^?[^}]*\}", "${v^} / ${v^^} case folding", "4.0"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*,,?[^}]*\}", "${v,} / ${v,,} case folding", "4.0"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*@[QEPAa]\}", "${v@Q} parameter transformation", "4.4"),
    (r"\bdeclare\s+-[A-Za-z]*A\b", "declare -A associative array", "4.0"),
    (r"\blocal\s+-[A-Za-z]*A\b", "local -A associative array", "4.0"),
    (r"\bmapfile\b", "mapfile builtin", "4.0"),
    (r"\breadarray\b", "readarray builtin", "4.0"),
    (r"&>>", "&>> append redirect", "4.0"),
    (r";;&", ";;& case fallthrough", "4.0"),
    (r"\bcoproc\b", "coproc keyword", "4.0"),
]

ALLOW_MARKER = "bash32-lint: allow"


def _shipped_scripts() -> list[Path]:
    return sorted(LIBRARY_SRC.rglob("*.sh"))


def _strip_comments(line: str) -> str:
    """Drop a trailing comment so prose about a construct is not a finding."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def test_shipped_scripts_exist() -> None:
    """Guard the guard: a path typo would make every check below vacuous."""
    scripts = _shipped_scripts()
    assert len(scripts) >= 10, (
        f"expected the library's shell hooks under {LIBRARY_SRC}, found {scripts}"
    )


@pytest.mark.parametrize("script", _shipped_scripts(), ids=lambda p: p.name)
def test_script_uses_no_bash4_only_construct(script: Path) -> None:
    """macOS ships bash 3.2; a 4.x-only construct is a runtime failure there."""
    findings = []
    for lineno, raw in enumerate(script.read_text().splitlines(), start=1):
        if ALLOW_MARKER in raw:
            continue
        code = _strip_comments(raw)
        for pattern, label, since in BASH4_CONSTRUCTS:
            if re.search(pattern, code):
                findings.append(
                    f"  {script.name}:{lineno}: {label} (bash {since}+) -> {raw.strip()}"
                )

    assert not findings, (
        "bash 4+ construct in a script that ships to users running bash 3.2:\n"
        + "\n".join(findings)
        + f"\n\nRewrite it, or append `# {ALLOW_MARKER}` if the line is genuinely unreachable on 3.2."
    )
