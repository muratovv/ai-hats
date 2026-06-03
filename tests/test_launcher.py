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

import pytest


pytestmark = pytest.mark.integration


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
    layout at <target>:
      - bin/python: exit 0
      - bin/pip: records args to <venv>/pip_called; on `install <…> ai-hats`
        also drops bin/ai-hats stub so the launcher's downstream delegate
        (HATS-337 heal-then-delegate) succeeds.
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
        '# Treat any `pip install …` invocation as installing ai-hats so the\n'
        '# launcher\'s downstream delegate (HATS-337) succeeds — covers both\n'
        '# PEP 508 "ai-hats @ url" and bare local-path target forms.\n'
        'if [[ "${1:-}" == "install" ]]; then\n'
        '    cat > "$(dirname "$0")/ai-hats" <<AHATS\n'
        '#!/usr/bin/env bash\n'
        'echo "venv-ai-hats: \\$*"\n'
        'exit 0\n'
        'AHATS\n'
        '    chmod +x "$(dirname "$0")/ai-hats"\n'
        'fi\n'
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
    """Fresh project (no ai-hats.yaml), default venv missing → exit 1 with a
    bootstrap hint pointing at `self init` (which now creates the venv +
    configures), NOT `self update` (HATS-612)."""
    res = _run(["status"], cwd=tmp_path)
    assert res.returncode == 1
    assert "venv missing at" in res.stderr
    assert ".agent/ai-hats/.venv" in res.stderr
    assert "ai-hats self init" in res.stderr
    assert "self update" not in res.stderr


def test_fallback_hint_is_update_when_yaml_present(tmp_path):
    """Initialized project (ai-hats.yaml present) whose default venv is missing
    → heal-recovery hint stays `self update`, not a re-init (HATS-612)."""
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\n"
    )
    res = _run(["status"], cwd=tmp_path)
    assert res.returncode == 1
    assert "venv missing at" in res.stderr
    assert "ai-hats self update" in res.stderr
    assert "self init" not in res.stderr


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
    """Default venv missing → heal creates venv + bare-installs ai-hats,
    then delegates to <venv>/bin/ai-hats for the rich python self update."""
    stub_dir = _fake_python3_with_venv_creator(tmp_path / "fake-bin")
    env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}
    res = _run(["self", "update"], cwd=tmp_path, env=env)
    assert res.returncode == 0, f"rc={res.returncode}\nstderr={res.stderr}\nstdout={res.stdout}"

    default_venv = tmp_path / ".agent" / "ai-hats" / ".venv"
    assert (default_venv / "bin" / "python").is_file()
    # Heal phase: bare pip install (no --upgrade — that's python's job).
    pip_marker = default_venv / "pip_called"
    assert pip_marker.is_file()
    text = pip_marker.read_text()
    assert "install" in text
    assert "ai-hats @" in text
    assert "--upgrade" not in text  # bare install only; python self update adds --force-reinstall
    # Delegate phase: <venv>/bin/ai-hats called with original argv.
    assert "venv-ai-hats: self update" in res.stdout


def test_self_init_creates_default_when_missing(tmp_path):
    """Fresh project, default venv missing → `self init` heals (creates venv +
    bare-installs ai-hats), then delegates to <venv>/bin/ai-hats so init can
    configure the project in one command — no separate `self update` first
    (HATS-612)."""
    stub_dir = _fake_python3_with_venv_creator(tmp_path / "fake-bin")
    env = {"PATH": f"{stub_dir}:{os.environ['PATH']}"}
    res = _run(["self", "init", "-r", "assistant", "-p", "claude"], cwd=tmp_path, env=env)
    assert res.returncode == 0, f"rc={res.returncode}\nstderr={res.stderr}\nstdout={res.stdout}"

    default_venv = tmp_path / ".agent" / "ai-hats" / ".venv"
    assert (default_venv / "bin" / "python").is_file()
    # Heal phase: bare pip install (no --upgrade).
    pip_marker = default_venv / "pip_called"
    assert pip_marker.is_file()
    assert "install" in pip_marker.read_text()
    # Delegate phase: <venv>/bin/ai-hats called with the original `self init` argv.
    assert "venv-ai-hats: self init -r assistant -p claude" in res.stdout


def test_self_update_recreates_broken_default(tmp_path):
    """Default venv dir exists but bin/python missing → heal recreates +
    bare-installs, then delegates to python self update."""
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
    assert "venv-ai-hats: self update" in res.stdout


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


def test_self_update_with_local_path_repo_url(tmp_path):
    """AI_HATS_REPO_URL без `://` (local path) → pip gets bare path, not the
    PEP 508 `ai-hats @ <path>` form (which requires a URL scheme and would
    fail pip install)."""
    stub_dir = _fake_python3_with_venv_creator(tmp_path / "fake-bin")
    fake_repo = tmp_path / "local-repo"
    fake_repo.mkdir()
    env = {
        "PATH": f"{stub_dir}:{os.environ['PATH']}",
        "AI_HATS_REPO_URL": str(fake_repo),
    }
    res = _run(["self", "update"], cwd=tmp_path, env=env)
    assert res.returncode == 0, res.stderr
    pip_marker = tmp_path / ".agent" / "ai-hats" / ".venv" / "pip_called"
    assert pip_marker.is_file()
    text = pip_marker.read_text()
    assert str(fake_repo) in text
    # local path target — bare path, not the PEP 508 `name @ url` form.
    assert "ai-hats @" not in text


def test_self_update_in_healthy_venv_delegates_to_python(tmp_path):
    """Healthy venv → heal is a no-op (no pip_called marker), launcher
    delegates straight to <venv>/bin/ai-hats for the rich python self
    update."""
    venv = tmp_path / ".agent" / "ai-hats" / ".venv"
    _fake_venv(venv, ai_hats_echo="healthy-ai-hats")
    res = _run(["self", "update"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    # Heal must NOT have called pip — venv was already healthy.
    assert not (venv / "pip_called").exists(), "heal should be a no-op on healthy venv"
    # Delegation happened — python ai-hats receives original argv.
    assert "healthy-ai-hats: self update" in res.stdout


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
    # Fresh project (no ai-hats.yaml) → bootstrap hint is `self init` (HATS-612).
    assert "ai-hats self init" in res.stderr


# ---------- HATS-647: versioned blue-green resolution + pin-at-spawn ----------


def _versions_layout(project_dir, sha, *, ai_hats_echo="ver-stub", make_dir=True, pointer=True):
    """Seed <project>/.agent/ai-hats/versions/<sha>/ (a fake venv) + current pointer."""
    versions = project_dir / ".agent" / "ai-hats" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    if make_dir:
        _fake_venv(versions / sha, ai_hats_echo=ai_hats_echo)
    if pointer:
        (versions / "current").write_text(f"{sha}\n")
    return versions


def test_resolve_versions_current_pointer(tmp_path):
    """Valid versions/current → launcher execs versions/<sha>/bin/ai-hats."""
    _versions_layout(tmp_path, "cafef00d", ai_hats_echo="ver-stub")
    res = _run(["status"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "ver-stub: status" in res.stdout


def test_versions_dangling_pointer_falls_back_to_legacy(tmp_path):
    """current points at a missing versions/<sha>/ → legacy .venv is used."""
    _versions_layout(tmp_path, "deadbeef", make_dir=False)  # pointer only, no dir
    _fake_venv(tmp_path / ".agent" / "ai-hats" / ".venv", ai_hats_echo="legacy-stub")
    res = _run(["status"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "legacy-stub: status" in res.stdout


def test_versions_incomplete_venv_falls_back_to_legacy(tmp_path):
    """current → versions/<sha>/ that exists but is broken (no bin/ai-hats) →
    legacy .venv is used (self-heal preserved; does not dead-end)."""
    versions = tmp_path / ".agent" / "ai-hats" / "versions"
    (versions / "deadbeef" / "bin").mkdir(parents=True)  # dir present, no ai-hats
    (versions / "current").write_text("deadbeef\n")
    _fake_venv(tmp_path / ".agent" / "ai-hats" / ".venv", ai_hats_echo="legacy-stub")
    res = _run(["status"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "legacy-stub: status" in res.stdout


def test_versions_multiline_pointer_does_not_forge_sha(tmp_path):
    """A multi-line pointer must NOT be squashed into a single fake sha; the
    first line ('cafe') has no matching dir → legacy .venv fallback."""
    versions = tmp_path / ".agent" / "ai-hats" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    _fake_venv(versions / "cafef00d", ai_hats_echo="ver-stub")  # the squashed name
    (versions / "current").write_text("cafe\nf00d\n")
    _fake_venv(tmp_path / ".agent" / "ai-hats" / ".venv", ai_hats_echo="legacy-stub")
    res = _run(["status"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "legacy-stub: status" in res.stdout
    assert "ver-stub" not in res.stdout


def test_versions_corrupt_pointer_falls_back_to_legacy(tmp_path):
    """Pointer content with a path separator never escapes → legacy .venv."""
    versions = tmp_path / ".agent" / "ai-hats" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    (versions / "current").write_text("../escape\n")
    _fake_venv(tmp_path / ".agent" / "ai-hats" / ".venv", ai_hats_echo="legacy-stub")
    res = _run(["status"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "legacy-stub: status" in res.stdout


def test_env_venv_beats_versions_current(tmp_path):
    """Explicit AI_HATS_VENV wins over a valid versions/current (HATS-339 override)."""
    _versions_layout(tmp_path, "cafef00d", ai_hats_echo="ver-stub")
    env_venv = tmp_path / "user-owned"
    _fake_venv(env_venv, ai_hats_echo="env-stub")
    res = _run(["status"], cwd=tmp_path, env={"AI_HATS_VENV": str(env_venv)})
    assert res.returncode == 0, res.stderr
    assert "env-stub: status" in res.stdout


def test_pin_exports_resolved_versioned_venv(tmp_path):
    """pin-at-spawn: the resolved versions/<sha> path is exported as AI_HATS_VENV
    so descendants inherit the exact same venv for the whole run."""
    versions = tmp_path / ".agent" / "ai-hats" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    sha = "cafef00d"
    _fake_venv(versions / sha)
    # Overwrite the ai-hats stub to echo the inherited pin.
    stub = versions / sha / "bin" / "ai-hats"
    stub.write_text('#!/usr/bin/env bash\necho "PIN=$AI_HATS_VENV"\nexit 0\n')
    _make_executable(stub)
    (versions / "current").write_text(f"{sha}\n")
    res = _run(["status"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert f"PIN={versions / sha}" in res.stdout
