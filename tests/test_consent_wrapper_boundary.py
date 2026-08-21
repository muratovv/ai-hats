from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOOL_SURFACES = (
    ROOT / "packages/ai-hats-rack/src/ai_hats_rack",
    ROOT / "packages/ai-hats-wt/src/ai_hats_wt",
    ROOT / "src/ai_hats/rack_wiring.py",
    ROOT / "src/ai_hats/wt_effects.py",
    ROOT / "src/ai_hats/cli/worktree.py",
    ROOT / "src/ai_hats/rack_cli_provider.py",
)
FORBIDDEN = re.compile(r"consent|AI_HATS_(?:PLAN|MERGE)_ACK", re.IGNORECASE)


def _source_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(
        candidate for candidate in path.rglob("*") if candidate.suffix in {".py", ".yaml"}
    )


def test_rack_and_wt_are_consent_unaware():
    offenders: list[str] = []
    for surface in TOOL_SURFACES:
        for path in _source_files(surface):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if FORBIDDEN.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")

    assert not offenders, "consent belongs outside rack/wt:\n" + "\n".join(offenders)
