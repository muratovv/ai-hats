"""The shape of a self-granted approval, in one place.

``ack_prefix_guard.py`` refuses a Bash line BINDING an ``AI_HATS_*`` ``_ACK`` /
``_OFF`` / ``_SKIP`` flag for the command after it, so text printing that line
as a way out of a block hands its reader a command the guard denies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: What a reader would actually run — the command is what separates an
#: instruction from a sentence, and only a roster tells them apart: the guard's
#: own parser reads `AI_HATS_X_ACK=1 is read from the environment` as a binding.
COMMANDS = ("git", "make", "rack", "ai-hats", "bash", "sh", "python3?", "pytest", r"\./")

RECIPE_RE = re.compile(
    r"AI_HATS_[A-Z0-9_]*(?:_ACK|_OFF|_SKIP)=1[ \t]+(?:" + "|".join(COMMANDS) + r")\b"
)

#: Where the shape is the subject rather than the advice: `safety-guard` is the
#: guard that refuses it, and quotes it to say so. Excluded by directory, not by
#: a line marker — there is one such home and a convention would invite more.
EXCLUDED_DIR = "safety-guard"

LIBRARY_RELPATH = "packages/ai-hats-library/src/ai_hats_library"

UNCOVERED = (
    "a recipe whose command is outside COMMANDS — the roster is what separates "
    "an instruction from a sentence about the flag, and it names what this "
    "repository's texts actually tell a reader to type",
    "`skills/safety-guard/**`: it quotes the refused shape to define it",
    "prose outside the library — `docs/`, `scripts/`, `tests/`: a flag recipe "
    "there is read by a maintainer of this checkout, not shipped into a role",
)


@dataclass(frozen=True)
class Finding:
    line: int
    text: str

    def __str__(self) -> str:
        return f"{self.line}: {self.text.strip()}"


def findings(text: str) -> list[Finding]:
    """Every line of ``text`` that prints a self-granted approval as a recipe."""
    return [
        Finding(number, line)
        for number, line in enumerate(text.splitlines(), 1)
        if RECIPE_RE.search(line)
    ]


def corpus(root: Path) -> list[Path]:
    """Every shipped library file, read as the prose an agent is handed.

    No suffix list: the library holds `.md` beside `.sh`, `.yaml` and `.py`, and
    a hook with no suffix at all would be the one that most needs judging. A
    binary appearing there simply never matches.
    """
    library = root / LIBRARY_RELPATH
    if not library.is_dir():
        return []
    return sorted(
        path
        for path in library.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and EXCLUDED_DIR not in path.relative_to(library).parts
    )


def scan(root: Path) -> list[str]:
    """``path:line: text`` for every recipe in the library under ``root``."""
    out: list[str] = []
    for path in corpus(root):
        for finding in findings(path.read_text(errors="ignore")):
            out.append(f"{path.relative_to(root)}:{finding}")
    return out
