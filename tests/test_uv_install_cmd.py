"""Unit: env-engine command builders emit uv with an explicit --python (HATS-763).

B1: a bare ``uv pip install`` resolves the *nearest discoverable* venv (e.g. a
cwd ``.venv``), NOT the interpreter we mean — unlike ``python -m pip`` which
always targets its own interpreter. So both builders MUST pin
``--python <interp>``. Pure functions → unit-tested here; the real subprocess is
exercised by the e2e gate.
"""

from __future__ import annotations

import sys

from ai_hats.cli.maintenance import _build_install_cmd, _build_update_cmd


def _python_arg(cmd: list[str]) -> str:
    assert "--python" in cmd, f"builder must pin --python: {cmd}"
    return cmd[cmd.index("--python") + 1]


def test_build_install_cmd_uv_url_with_ref():
    cmd = _build_install_cmd("/v/bin/python", "git+ssh://x/ai-hats.git", "abc")
    assert cmd[:3] == ["uv", "pip", "install"]
    assert _python_arg(cmd) == "/v/bin/python"
    assert "--reinstall" in cmd
    assert cmd[-1] == "ai-hats @ git+ssh://x/ai-hats.git@abc"
    assert "-m" not in cmd  # no `python -m pip` form


def test_build_install_cmd_uv_local_path():
    cmd = _build_install_cmd("/v/bin/python", "/local/path", "abc")
    assert cmd[:3] == ["uv", "pip", "install"]
    assert _python_arg(cmd) == "/v/bin/python"
    assert cmd[-1] == "/local/path"  # local path: pip/uv take no @ref


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
