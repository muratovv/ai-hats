"""e2e (HATS-1806)

flow:   a nested HITL session materializes consent wrappers over an outer session
cmds:
    rack --help
expect: the inner wrapper resolves the canonical executable and invokes it once
why:    recording the outer wrapper as the original recursively spawns wrappers
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.git import git as _git

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "ai-hats.yaml").write_text("task_prefix: SBX\n", encoding="utf-8")
    (root / ".gitignore").write_text(".agent/\nai-hats.yaml\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init", "--allow-empty")
    return root


def test_nested_session_resolves_original_past_outer_wrapper(tmp_path: Path, project: Path) -> None:
    """HATS-1806: the inner original bypasses every inherited wrapper."""
    from ai_hats.consent_wrapper import CONFIG_ENV
    from _helpers.env import checkout_pythonpath
    from _helpers.sessions import stand_in_wrapped_session

    canonical_bin = tmp_path / "canonical-bin"
    canonical_bin.mkdir()
    calls = tmp_path / "rack-calls"
    canonical_rack = _executable(
        canonical_bin / "rack",
        "import os\n"
        "from pathlib import Path\n"
        "path = Path(os.environ['HATS_1806_CALLS'])\n"
        "with path.open('a', encoding='utf-8') as stream:\n"
        "    stream.write('rack\\n')\n",
    )
    _executable(canonical_bin / "ai-hats", "raise SystemExit(0)\n")

    env = os.environ.copy()
    env.pop(CONFIG_ENV, None)
    env["AI_HATS_CACHE_HOME"] = str(tmp_path / "cache")
    env["HATS_1806_CALLS"] = str(calls)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    env["PATH"] = os.pathsep.join((str(canonical_bin), os.defpath))

    outer = stand_in_wrapped_session(env, project, "outer")
    inner = stand_in_wrapped_session(outer.copy(), project, "inner")
    config = json.loads(Path(inner[CONFIG_ENV]).read_text(encoding="utf-8"))

    assert Path(config["originals"]["rack"]).resolve() == canonical_rack.resolve()
    assert not calls.exists()

    completed = subprocess.run(  # noqa: S603,S607 - session PATH selects the wrapper
        ["rack", "--help"],
        cwd=project,
        env=inner,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0, completed.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == ["rack"]
