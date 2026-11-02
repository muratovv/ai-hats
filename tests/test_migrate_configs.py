"""Part 3 of the migration: customizations become override files, and a
``- <composite>`` names the member someone else also brings."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "migrate_experimental.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60
    )


def test_a_subtracted_composite_names_the_shared_member(tmp_path: Path):
    root = tmp_path / "flat"
    root.mkdir()
    (root / "A.yaml").write_text("schema_version: 1\ntype: composite\nexpr: f1 + f2 + f3\n")
    (root / "B.yaml").write_text("schema_version: 1\ntype: composite\nexpr: f2 + f3 + f4\n")
    (root / "use.yaml").write_text("schema_version: 1\ntype: composite\nexpr: A + B - A\n")
    result = _run("--carried-away", str(root))
    assert result.returncode == 0, result.stderr
    assert "carried away: f2" in result.stdout
    assert "carried away: f3" in result.stdout


def test_customizations_become_overrides_and_an_unknown_role_is_named(tmp_path: Path):
    home = tmp_path / "home"
    (home / "user-rules").mkdir(parents=True)
    (home / ".githooks" / "pre-commit.d").mkdir(parents=True)
    (home / "customizations.yaml").write_text(
        "customizations:\n"
        "  maintainer:\n"
        "    add:\n"
        "      traits: [personal-workflow]\n"
        "  go-dev-full:\n"
        "    add:\n"
        "      traits: [personal-workflow]\n"
    )
    (home / "user-rules" / "no-force-push.md").write_text("Do not force-push.\n")
    (home / "library_paths.yaml").write_text("paths:\n  - ~/dev/ai-hats-custom\n")
    (home / "ai-hats.yaml").write_text("default_role: assistant\nactive_role: maintainer\n")
    (home / ".githooks" / "pre-commit.d" / "check.sh").write_text("#!/bin/sh\nexit 0\n")
    known = tmp_path / "known"
    known.mkdir()
    (known / "maintainer.yaml").write_text("schema_version: 1\ntype: composite\nexpr: base\n")
    out = tmp_path / "out"
    result = _run("--home", str(home), "--out", str(out), "--known", str(known))
    assert result.returncode == 0, result.stderr
    assert "unknown role: go-dev-full" in result.stdout
    text = (out / "overrides" / "customize-maintainer.yaml").read_text()
    assert 'apply: "^maintainer$"' in text
    assert "personal-workflow" in text
    assert "no-force-push" in (out / "rules" / "no-force-push.md").read_text()
    assert 'apply: ".*"' in (out / "overrides" / "user-rules.yaml").read_text()
    assert "~/dev/ai-hats-custom" in (out / "config.yaml").read_text()
    assert "default: assistant" in (out / "ai-hats.yaml").read_text()
    assert "active: maintainer" in (out / "local.yaml").read_text()
    assert "at: pre-commit" in (out / "hooks" / "dropin" / "check.yaml").read_text()
    first = {
        path.relative_to(out).as_posix(): path.read_bytes()
        for path in out.rglob("*")
        if path.is_file()
    }
    again = _run("--home", str(home), "--out", str(out), "--known", str(known))
    assert again.returncode == 0
    second = {
        path.relative_to(out).as_posix(): path.read_bytes()
        for path in out.rglob("*")
        if path.is_file()
    }
    assert first == second
