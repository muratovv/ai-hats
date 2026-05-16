"""Tests for scripts/ai-hats-launcher (HATS-339).

Strategy: spawn the launcher via subprocess against fixture-built fake
venv layouts. No real `pip install` (network + slow); python3/pip are
stubbed via PATH prepend where the launcher would invoke them.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "scripts" / "ai-hats-launcher"


# ---------- helpers ----------


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_venv(venv_path: Path, *, ai_hats_echo: str = "ai-hats-stub") -> None:
    """Build a venv layout that satisfies the launcher's pre-checks.

    Creates bin/python (stub, exit 0), bin/ai-hats (echoes ai_hats_echo
    plus args), and bin/pip (records args to '../pip_called' marker).
    """
    bindir = venv_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)

    python_stub = bindir / "python"
    python_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    _make_executable(python_stub)

    ai_hats_stub = bindir / "ai-hats"
    ai_hats_stub.write_text(
        f'#!/usr/bin/env bash\necho "{ai_hats_echo}: $*"\nexit 0\n'
    )
    _make_executable(ai_hats_stub)

    pip_stub = bindir / "pip"
    pip_stub.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$@" > "$(dirname "$0")/../pip_called"\n'
        'exit 0\n'
    )
    _make_executable(pip_stub)


def _fake_python3_with_venv_creator(stub_dir: Path) -> Path:
    """Create a python3 stub that emulates `python3 -m venv <path>`.

    When called as `python3 -m venv <target>`, builds a minimal venv
    layout at <target> (bin/python exits 0, bin/pip records args).
    Returns the directory to prepend to PATH.
    """
    stub_dir.mkdir(parents=True, exist_ok=True)
    py = stub_dir / "python3"
    py.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "${1:-}" == "-m" && "${2:-}" == "venv" && -n "${3:-}" ]]; then\n'
        '    target="$3"\n'
        '    mkdir -p "$target/bin"\n'
        '    cat > "$target/bin/python" <<\'PY\'\n'
        '#!/usr/bin/env bash\n'
        'exit 0\n'
        'PY\n'
        '    chmod +x "$target/bin/python"\n'
        '    cat > "$target/bin/pip" <<\'PIP\'\n'
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$@" > "$(dirname "$0")/../pip_called"\n'
        'exit 0\n'
        'PIP\n'
        '    chmod +x "$target/bin/pip"\n'
        '    exit 0\n'
        'fi\n'
        'exit 0\n'
    )
    _make_executable(py)
    return stub_dir


def _run(args, *, cwd, env=None):
    base_env = os.environ.copy()
    # Default: clean AI_HATS_VENV unless caller explicitly sets it.
    base_env.pop("AI_HATS_VENV", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        [str(LAUNCHER), *args],
        cwd=str(cwd),
        env=base_env,
        capture_output=True,
        text=True,
    )


# ---------- venv resolution (no self update) ----------


def test_resolve_default_no_yaml_no_env_missing_venv(tmp_path):
    """Empty project, default venv missing → exit 1 with hint."""
    res = _run(["status"], cwd=tmp_path)
    assert res.returncode == 1
    assert "venv missing at" in res.stderr
    assert ".agent/ai-hats/.venv" in res.stderr
    assert "ai-hats self update" in res.stderr


def test_resolve_default_with_healthy_venv_execs_stub(tmp_path):
    """Default venv exists → launcher execs <venv>/bin/ai-hats with argv."""
    venv = tmp_path / ".agent" / "ai-hats" / ".venv"
    _fake_venv(venv, ai_hats_echo="default-stub")
    res = _run(["status", "--verbose"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "default-stub: status --verbose" in res.stdout


def test_resolve_yaml_relative_venv_path(tmp_path):
    """yaml.venv_path relative → resolved against $(pwd)."""
    venv = tmp_path / "myvenv"
    _fake_venv(venv, ai_hats_echo="rel-stub")
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\n"
        "venv_path: myvenv\nprovider: claude\n"
    )
    res = _run(["whoami"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "rel-stub: whoami" in res.stdout


def test_resolve_yaml_absolute_venv_path(tmp_path):
    """yaml.venv_path absolute → used as-is."""
    venv = tmp_path / "abs-venv"
    _fake_venv(venv, ai_hats_echo="abs-stub")
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\n"
        f"venv_path: {venv}\nprovider: claude\n"
    )
    res = _run(["xx"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "abs-stub: xx" in res.stdout


def test_resolve_yaml_with_inline_comment_stripped(tmp_path):
    """yaml.venv_path with trailing `# comment` → comment dropped."""
    venv = tmp_path / "commented"
    _fake_venv(venv, ai_hats_echo="cmt-stub")
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\n"
        "venv_path: commented  # my override\nprovider: claude\n"
    )
    res = _run(["x"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "cmt-stub: x" in res.stdout


def test_env_overrides_yaml(tmp_path):
    """AI_HATS_VENV beats yaml.venv_path."""
    yaml_venv = tmp_path / "from-yaml"
    env_venv = tmp_path / "from-env"
    _fake_venv(yaml_venv, ai_hats_echo="yaml-stub")
    _fake_venv(env_venv, ai_hats_echo="env-stub")
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\n"
        "venv_path: from-yaml\nprovider: claude\n"
    )
    res = _run(["whoami"], cwd=tmp_path, env={"AI_HATS_VENV": str(env_venv)})
    assert res.returncode == 0, res.stderr
    assert "env-stub: whoami" in res.stdout


# ---------- self update branches ----------


def test_self_update_creates_default_when_missing(tmp_path):
    """Default venv missing → launcher invokes python3 -m venv then pip."""
    stub_dir = _fake_python3_with_venv_creator(tmp_path / "fake-bin")
    env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}
    res = _run(["self", "update"], cwd=tmp_path, env=env)
    assert res.returncode == 0, f"rc={res.returncode}\nstderr={res.stderr}\nstdout={res.stdout}"

    default_venv = tmp_path / ".agent" / "ai-hats" / ".venv"
    assert (default_venv / "bin" / "python").is_file()
    pip_marker = default_venv / "pip_called"
    assert pip_marker.is_file()
    text = pip_marker.read_text()
    assert "install" in text
    assert "--upgrade" in text
    assert "ai-hats @" in text


def test_self_update_recreates_broken_default(tmp_path):
    """Default venv dir exists but bin/python missing → recreates."""
    venv = tmp_path / ".agent" / "ai-hats" / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "marker_old").write_text("pre-existing")
    stub_dir = _fake_python3_with_venv_creator(tmp_path / "fake-bin")
    env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}
    res = _run(["self", "update"], cwd=tmp_path, env=env)
    assert res.returncode == 0, res.stderr
    assert (venv / "bin" / "python").is_file()
    assert not (venv / "marker_old").exists(), "broken venv was not wiped"
    assert "recreating" in res.stderr


def test_self_update_refuses_recreate_override(tmp_path):
    """Override venv broken → exits 1 with user-owned explanation."""
    override = tmp_path / "user-venv"
    override.mkdir()  # exists but no bin/python — broken
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\n"
        f"venv_path: {override}\nprovider: claude\n"
    )
    res = _run(["self", "update"], cwd=tmp_path)
    assert res.returncode == 1
    assert "override venv" in res.stderr
    assert str(override) in res.stderr
    assert "user-owned" in res.stderr


def test_self_update_in_healthy_venv_calls_pip(tmp_path):
    """Healthy venv → skip create, exec pip install --upgrade ai-hats."""
    venv = tmp_path / ".agent" / "ai-hats" / ".venv"
    _fake_venv(venv)
    res = _run(["self", "update"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    pip_marker = venv / "pip_called"
    assert pip_marker.is_file()
    text = pip_marker.read_text()
    assert "install" in text
    assert "--upgrade" in text
    assert "ai-hats @" in text


# ---------- exec fall-through edge cases ----------


def test_exec_fails_when_ai_hats_binary_missing(tmp_path):
    """Venv has python but no ai-hats → friendly error, exit 1."""
    venv = tmp_path / ".agent" / "ai-hats" / ".venv"
    (venv / "bin").mkdir(parents=True)
    python_stub = venv / "bin" / "python"
    python_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    _make_executable(python_stub)
    res = _run(["status"], cwd=tmp_path)
    assert res.returncode == 1
    assert "ai-hats binary is missing" in res.stderr
    assert "ai-hats self update" in res.stderr
