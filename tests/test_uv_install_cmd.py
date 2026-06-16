"""Unit: env-engine command builders emit uv with an explicit --python (HATS-763).

B1: a bare ``uv pip install`` resolves the *nearest discoverable* venv (e.g. a
cwd ``.venv``), NOT the interpreter we mean — unlike ``python -m pip`` which
always targets its own interpreter. So both builders MUST pin
``--python <interp>``. Pure functions → unit-tested here; the real subprocess is
exercised by the e2e gate.
"""

from __future__ import annotations

import sys

import pytest

from ai_hats.cli.maintenance import (
    _build_install_cmd,
    _build_update_cmd,
    _require_uv,
)


def test_require_uv_fails_loud_when_missing(monkeypatch):
    """D2: uv absent on PATH → fail loud, exit non-zero, no pip fallback."""
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(SystemExit) as exc:
        _require_uv()
    assert exc.value.code == 1


def test_require_uv_passes_when_present(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/uv")
    _require_uv()  # no raise


def _python_arg(cmd: list[str]) -> str:
    assert "--python" in cmd, f"builder must pin --python: {cmd}"
    return cmd[cmd.index("--python") + 1]


def test_build_install_cmd_passes_spec_through():
    # HATS-764: _build_install_cmd is a thin uv wrapper now — install_spec is
    # pre-shaped by the channel resolver; the builder passes it through verbatim.
    cmd = _build_install_cmd("/v/bin/python", "ai-hats @ git+https://x/ai-hats.git@abc")
    assert cmd[:3] == ["uv", "pip", "install"]
    assert _python_arg(cmd) == "/v/bin/python"
    assert "--reinstall" in cmd
    assert cmd[-1] == "ai-hats @ git+https://x/ai-hats.git@abc"
    assert "-m" not in cmd  # no `python -m pip` form


def test_build_install_cmd_local_path_spec():
    cmd = _build_install_cmd("/v/bin/python", "/local/path")
    assert cmd[:3] == ["uv", "pip", "install"]
    assert _python_arg(cmd) == "/v/bin/python"
    assert cmd[-1] == "/local/path"


def test_build_update_cmd_pins_running_interpreter():
    """B1: _build_update_cmd had no python arg; must pin --python sys.executable."""
    cmd = _build_update_cmd()
    assert cmd[:3] == ["uv", "pip", "install"]
    assert _python_arg(cmd) == sys.executable
    assert "--reinstall" in cmd
    assert "-m" not in cmd


def test_build_update_cmd_with_ref_targets_running_interpreter():
    cmd = _build_update_cmd(ref="v1.2.3")
    assert _python_arg(cmd) == sys.executable
    assert cmd[-1].endswith("@v1.2.3")
