"""HATS-815 — the leftover hook-sidecar bump diagnostic.

``Assembler._warn_leftover_hook_sidecars`` is wired into ``_run_diagnostics``
(user-initiated bump / self update / re-init only). Per-session ``set_role``
never calls ``_run_diagnostics`` (HATS-469 R3), so the detector is inherently
silent there — the proactive WARN belongs to the maintenance path, the 814
compose-guard owns the per-session hard-fail.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


def _lib_with_skill(root: Path, name: str, sidecar: str | None) -> Path:
    d = root / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n# {name}\n")
    if sidecar is not None:
        (d / "metadata.yaml").write_text(sidecar)
    return root


def _assembler(tmp_path: Path) -> Assembler:
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)
    return Assembler(project)


def test_warn_emitted_for_hook_sidecar(tmp_path, capsys):
    lib = _lib_with_skill(
        tmp_path / "lib",
        "demo",
        "name: demo\ngit_hooks:\n  pre-commit:\n    - g.sh\n",
    )
    asm = _assembler(tmp_path)
    asm.library_paths = [lib]  # isolate the scan from builtin/global layers

    assert asm._warn_leftover_hook_sidecars() is True
    err = capsys.readouterr().err
    assert "demo" in err
    assert "ai_hats:" in err
    assert "WARN" in err


def test_silent_when_clean(tmp_path, capsys):
    lib = _lib_with_skill(tmp_path / "lib", "plain", "name: plain\nauthor: x\n")
    asm = _assembler(tmp_path)
    asm.library_paths = [lib]

    assert asm._warn_leftover_hook_sidecars() is False
    assert capsys.readouterr().err == ""


def test_run_diagnostics_wires_detector(tmp_path, capsys):
    lib = _lib_with_skill(
        tmp_path / "lib",
        "guard",
        "name: guard\nruntime_hooks:\n  PreToolUse:\n"
        "    - matcher: Bash\n      script: h.sh\n",
    )
    asm = _assembler(tmp_path)
    asm.library_paths = [lib]

    asm._run_diagnostics()
    err = capsys.readouterr().err
    assert "guard" in err
    assert "metadata.yaml still carries" in err
