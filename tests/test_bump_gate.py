"""Tests for HATS-330 — pre-bump local-install compatibility gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats import __version__
from ai_hats.assembler import (
    BumpAbortError,
    _check_local_install_compatibility,
    _detect_local_ai_hats,
    _local_ai_hats_version,
)


@pytest.fixture()
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def _stub_local_ai_hats(project: Path, *, version: str | None, venv: str = ".venv") -> Path:
    """Create a fake <project>/<venv>/bin/ai-hats stub that prints `version`.

    If ``version`` is None, the stub exits non-zero (simulating broken install).
    """
    venv_bin = project / venv / "bin"
    venv_bin.mkdir(parents=True)
    stub = venv_bin / "ai-hats"
    if version is None:
        stub.write_text("#!/usr/bin/env bash\nexit 1\n")
    else:
        stub.write_text(f"#!/usr/bin/env bash\necho 'ai-hats, version {version}'\n")
    stub.chmod(0o755)
    return stub


# ---------- detection helpers ----------


def test_detect_no_local_venv(project):
    assert _detect_local_ai_hats(project) is None


def test_detect_dotvenv(project):
    stub = _stub_local_ai_hats(project, version="1.0.0", venv=".venv")
    assert _detect_local_ai_hats(project) == stub


def test_detect_venv_fallback(project):
    stub = _stub_local_ai_hats(project, version="1.0.0", venv="venv")
    assert _detect_local_ai_hats(project) == stub


def test_local_version_parses_click_output(project):
    stub = _stub_local_ai_hats(project, version="0.3.1.dev42+abcdef")
    assert _local_ai_hats_version(stub) == "0.3.1.dev42+abcdef"


def test_local_version_none_on_subprocess_failure(project):
    stub = _stub_local_ai_hats(project, version=None)
    assert _local_ai_hats_version(stub) is None


def test_local_version_none_on_oserror(project, monkeypatch):
    stub = _stub_local_ai_hats(project, version="1.0.0")

    def _raise(*_args, **_kwargs):
        raise OSError("simulated")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert _local_ai_hats_version(stub) is None


# ---------- gate behaviour ----------


def test_gate_passes_when_no_local_venv(project):
    """No detection target → no-op."""
    _check_local_install_compatibility(project)  # must not raise


def test_gate_passes_when_versions_match(project):
    _stub_local_ai_hats(project, version=__version__)
    _check_local_install_compatibility(project)  # must not raise


def test_gate_aborts_on_version_mismatch(project):
    _stub_local_ai_hats(project, version="0.0.1.preposterous")
    with pytest.raises(BumpAbortError) as exc_info:
        _check_local_install_compatibility(project)
    msg = str(exc_info.value)
    assert "0.0.1.preposterous" in msg
    assert __version__ in msg
    assert "pip install -U" in msg
    assert "--force-allow-mismatch" in msg


def test_gate_bypassed_by_force_flag(project):
    _stub_local_ai_hats(project, version="0.0.1.preposterous")
    _check_local_install_compatibility(project, force_allow_mismatch=True)
    # No raise → bypass works.


def test_gate_soft_warns_when_local_version_unknown(project, capsys):
    """Local install present but `--version` fails → warn on stderr, continue."""
    _stub_local_ai_hats(project, version=None)
    _check_local_install_compatibility(project)  # no raise
    err = capsys.readouterr().err
    assert "could not query its version" in err


# ---------- Assembler.bump integration ----------


def test_bump_aborts_when_local_mismatch(project, monkeypatch):
    """Assembler.bump propagates BumpAbortError when the gate refuses."""
    from ai_hats.assembler import Assembler

    # Minimal yaml so Assembler can construct without exploding.
    (project / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\n"
        "active_role: assistant\ndefault_role: ''\nlibrary_paths: []\n"
    )
    _stub_local_ai_hats(project, version="0.0.1.preposterous")

    asm = Assembler(project)
    with pytest.raises(BumpAbortError):
        asm.bump()


def test_bump_force_flag_bypasses_gate(project, monkeypatch):
    """Assembler.bump(force_allow_mismatch=True) skips the gate even on mismatch.

    We monkeypatch the heavy downstream calls so the test only verifies the
    gate doesn't trip — full bump semantics are covered in test_assembler.py.
    """
    from ai_hats.assembler import Assembler

    (project / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\n"
        "active_role: ''\ndefault_role: ''\nlibrary_paths: []\n"
    )
    _stub_local_ai_hats(project, version="0.0.1.preposterous")

    asm = Assembler(project)
    # active_role='' → bump returns None without touching role-resolution.
    result = asm.bump(force_allow_mismatch=True)
    assert result is None
