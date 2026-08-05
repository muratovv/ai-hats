"""``--dry-run`` reports the launch and writes nothing (HATS-1211).

The side effect under test is an ABSENCE: after a dry-run the project tree is
byte-identical. A write escaping the materialization port happens for real here
and fails this test.

Moved out of ``tests/e2e/`` by HATS-1493 — it never drove the binary, and the
``dev_rule_e2e_gate`` claim it used to carry was false (see the card).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIB_CORE = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library" / "core"
LIB_USAGE = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library" / "usage"


def _fingerprint(project: Path) -> dict[str, str]:
    """Digest both roots — the cache left the project in HATS-1398, and a
    project-only walk would pass while a build wrote freely to the real target."""
    from ai_hats.paths import cache_root

    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for root in (project, cache_root(project))
        if root.is_dir()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIB_CORE), str(LIB_USAGE)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(proj / PROJECT_CONFIG)
    asm = Assembler(proj, library_paths=[LIB_CORE, LIB_USAGE])
    asm.init()
    asm.set_role("maintainer", provider_name="claude")
    monkeypatch.chdir(proj)
    import ai_hats._bootstrap as boot

    monkeypatch.setattr(boot, "bootstrap_or_die", lambda: None)
    return proj


def test_dry_run_reports_the_launch_without_spawning(project: Path):
    result = CliRunner().invoke(main, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert "run_mode=hitl" in result.output
    assert "--system-prompt-file" in result.output
    assert "prompt.md" in result.output


def test_dry_run_leaves_the_project_byte_identical(project: Path):
    before = _fingerprint(project)

    result = CliRunner().invoke(main, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert _fingerprint(project) == before


def test_dry_run_json_is_machine_readable_and_hides_env_values(project: Path):
    result = CliRunner().invoke(main, ["--dry-run-json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run_mode"] == "hitl"
    assert payload["provider"] == "claude"
    assert payload["materialized"], "the report must list what would be written"
    assert "env_keys" in payload and "env" not in payload
    assert payload["escapes"] == []
