"""logo-design: the audit sees a bad mark, and the reference library is absent."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/usage/skills/logo-design"
AUDIT = SKILL / "scripts" / "svg_audit.py"
PREVIEW = SKILL / "scripts" / "preview_sheet.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "logo-design"


def _findings(name: str) -> set[str]:
    proc = subprocess.run(
        [sys.executable, str(AUDIT), "--json", str(FIXTURES / name)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    return {item["code"] for item in payload[0]["findings"]}


def test_bad_mark_reports_live_text_and_near_miss():
    codes = _findings("bad.svg")
    assert "live-text" in codes
    assert "near-miss-angle" in codes


def test_clean_rect_omits_those_codes():
    codes = _findings("clean.svg")
    assert "live-text" not in codes
    assert "near-miss-angle" not in codes


def test_refs_industry_says_the_library_is_not_shipped(tmp_path: Path):
    out = tmp_path / "sheet.html"
    proc = subprocess.run(
        [
            sys.executable,
            str(PREVIEW),
            str(FIXTURES / "clean.svg"),
            "--refs-industry",
            "payments",
            "-o",
            str(out),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "not shipped" in proc.stderr
    assert not out.exists()


def test_reference_library_is_absent():
    names = {path.relative_to(SKILL).as_posix() for path in SKILL.rglob("*") if path.is_file()}
    assert "scripts/svg_audit.py" in names
    assert "scripts/search_library.py" not in names
    assert "scripts/build_catalog.py" not in names
    assert "catalog.json" not in names
    assert not any(name.startswith("assets/") for name in names)
