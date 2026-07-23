"""Tests for src/ai_hats/_bootstrap.py — startup self-heal (HATS-213).

T1  happy path: nothing missing.
T2  detection: monkeypatched find_spec returns None for ptyprocess.
T3  auto-sync with pyproject via importlib.metadata.requires.
T4  bootstrap_or_die success: pip ok → os.execv called.
T5  bootstrap_or_die failure: pip fails → SystemExit(1) + rescue line on stderr.
T6  verify_after_install success: heals; no re-exec.
T7  verify_after_install failure: pip fails → exit 1.
T8  transitional wave: missing dep → bootstrap_or_die → execv (one user-visible action).
T9  future-dep cycle: new dep declared → verify_after_install installs it.
T10 integrity: stale first-party provider entry point fails the verify.
T11 integrity: a failing out-of-tree provider plugin does NOT fail the verify.
T12 integrity: an ai_hats module that no longer imports fails the verify.
"""

from __future__ import annotations

import sys

import pytest

from ai_hats import _bootstrap


# ---------- helpers ----------


def _force_missing(monkeypatch, missing_imports: set[str]) -> None:
    """Make find_spec return None for every name in missing_imports."""
    real = _bootstrap.importlib.util.find_spec

    def fake(name, *a, **kw):
        if name in missing_imports:
            return None
        return real(name, *a, **kw)

    monkeypatch.setattr(_bootstrap.importlib.util, "find_spec", fake)


def _fixed_requires(monkeypatch, reqs: list[str]) -> None:
    monkeypatch.setattr(
        _bootstrap.importlib.metadata,
        "requires",
        lambda dist: list(reqs),
    )


# ---------- T1 ----------


def test_t1_happy_path_no_missing():
    """All declared runtime deps are importable in the test environment."""
    assert _bootstrap.find_missing_runtime_deps() == []


# ---------- T2 ----------


def test_t2_detection_finds_missing(monkeypatch):
    """Monkeypatch ptyprocess away → it surfaces as missing."""
    _force_missing(monkeypatch, {"ptyprocess"})
    missing = _bootstrap.find_missing_runtime_deps()
    assert "ptyprocess" in missing


# ---------- T3 ----------


def test_t3_auto_sync_with_pyproject(monkeypatch):
    """Distribution → import-name mapping respects PEP 503 + pyyaml override."""
    _fixed_requires(monkeypatch, ["foobar>=1.0", "PyYAML>=6.0", "click>=8.1; extra == 'dev'"])
    deps = _bootstrap.expected_runtime_deps()
    pairs = {dist.lower(): imp for dist, imp in deps}
    assert pairs.get("foobar") == "foobar"
    # pyyaml override → import name "yaml"
    pyyaml_imp = next(imp for dist, imp in deps if dist.lower() == "pyyaml")
    assert pyyaml_imp == "yaml"
    # extras-only requirement is filtered out
    assert "click" not in pairs


def test_t3b_handles_extras_brackets(monkeypatch):
    _fixed_requires(monkeypatch, ["pkg[extra]>=1.0"])
    deps = _bootstrap.expected_runtime_deps()
    assert deps == [("pkg[extra]>=1.0".split("[")[0], "pkg")] or deps[0][1] == "pkg"


# ---------- T4 ----------


def test_t4_bootstrap_or_die_success_path(monkeypatch):
    """Missing dep → pip succeeds → os.execv invoked with fresh interpreter."""
    _force_missing(monkeypatch, {"ptyprocess"})

    pip_calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        pip_calls.append(list(cmd))
        return type("R", (), {"returncode": 0})()

    execv_calls: list[tuple] = []

    def fake_execv(path, args):
        execv_calls.append((path, list(args)))

    monkeypatch.setattr(_bootstrap.subprocess, "run", fake_run)
    monkeypatch.setattr(_bootstrap.os, "execv", fake_execv)
    monkeypatch.setattr(sys, "argv", ["ai-hats", "status"])

    _bootstrap.bootstrap_or_die()

    assert len(pip_calls) == 1
    cmd = pip_calls[0]
    # HATS-763: uv engine. `--python sys.executable` targets THIS interp (B1).
    assert cmd[:3] == ["uv", "pip", "install"]
    assert cmd[cmd.index("--python") + 1] == sys.executable
    assert "ptyprocess" in cmd

    assert len(execv_calls) == 1
    path, args = execv_calls[0]
    assert path == sys.executable
    assert args == [sys.executable, "-m", "ai_hats", "status"]


# ---------- T5 ----------


def test_t5_bootstrap_or_die_failure_path(monkeypatch, capsys):
    """pip fails → SystemExit(1), rescue command surfaced on stderr."""
    _force_missing(monkeypatch, {"ptyprocess"})

    monkeypatch.setattr(
        _bootstrap.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 1})(),
    )

    def boom_execv(*a, **kw):
        raise AssertionError("execv must NOT be called on pip failure")

    monkeypatch.setattr(_bootstrap.os, "execv", boom_execv)

    with pytest.raises(SystemExit) as exc:
        _bootstrap.bootstrap_or_die()
    assert exc.value.code == 1

    err = capsys.readouterr().err
    assert "ptyprocess" in err
    assert "uv pip install" in err  # HATS-763: rescue is a uv command


# ---------- T6 ----------


def test_t6_verify_after_install_success(monkeypatch, capsys):
    """Stage-2 verify heals on success without re-exec."""
    state = {"missing": True}

    def fake_run(cmd, **kw):
        state["missing"] = False
        return type("R", (), {"returncode": 0})()

    real_find = _bootstrap.importlib.util.find_spec

    def fake_find_spec(name, *a, **kw):
        if name == "ptyprocess" and state["missing"]:
            return None
        return real_find(name, *a, **kw)

    monkeypatch.setattr(_bootstrap.subprocess, "run", fake_run)
    monkeypatch.setattr(_bootstrap.importlib.util, "find_spec", fake_find_spec)

    def boom_execv(*a, **kw):
        raise AssertionError("verify must not re-exec")

    monkeypatch.setattr(_bootstrap.os, "execv", boom_execv)

    rc = _bootstrap.verify_after_install()
    assert rc == 0


# ---------- T7 ----------


def test_t7_verify_after_install_failure(monkeypatch):
    """Stage-2 verify exits 1 when pip fails to install missing dep."""
    _force_missing(monkeypatch, {"ptyprocess"})
    monkeypatch.setattr(
        _bootstrap.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 1})(),
    )
    rc = _bootstrap.verify_after_install()
    assert rc == 1


# ---------- T8 ----------


def test_t8_transitional_wave_one_action(monkeypatch):
    """User upgrades from pre-HATS-207 wheel → first run heals + re-execs."""
    _force_missing(monkeypatch, {"ptyprocess"})

    monkeypatch.setattr(
        _bootstrap.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0})(),
    )
    execs: list = []
    monkeypatch.setattr(
        _bootstrap.os,
        "execv",
        lambda p, a: execs.append((p, list(a))),
    )
    monkeypatch.setattr(sys, "argv", ["ai-hats"])

    _bootstrap.bootstrap_or_die()

    # One user-visible action (the original `ai-hats` invocation) → exactly
    # one re-exec, no SystemExit.
    assert len(execs) == 1


# ---------- T9 ----------


def test_t9_future_dep_cycle(monkeypatch):
    """New dep declared in pyproject → stage-2 verify finds & heals it."""
    _fixed_requires(monkeypatch, ["futuredep>=1.0", "ptyprocess>=0.7", "click>=8.1"])
    _force_missing(monkeypatch, {"futuredep"})

    pip_calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        pip_calls.append(list(cmd))
        # Pretend install succeeded — flip futuredep to "available" by
        # patching find_spec in the second pass.
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(_bootstrap.subprocess, "run", fake_run)

    # After heal, find_spec must report futuredep as installed → use a
    # mutable counter to flip behaviour across calls.
    state = {"healed": False}
    real_find = _bootstrap.importlib.util.find_spec

    def fake_find(name, *a, **kw):
        if name == "futuredep":
            return None if not state["healed"] else object()
        return real_find(name, *a, **kw)

    monkeypatch.setattr(_bootstrap.importlib.util, "find_spec", fake_find)

    # First call to find_missing surfaces futuredep, attempt_self_heal flips
    # the flag, second find_missing returns empty → verify exits 0.
    def fake_run_then_heal(cmd, **kw):
        pip_calls.append(list(cmd))
        state["healed"] = True
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(_bootstrap.subprocess, "run", fake_run_then_heal)

    rc = _bootstrap.verify_after_install()
    assert rc == 0
    assert any("futuredep" in c for c in pip_calls)


# ---------- T10-T12: install integrity (HATS-1116) ----------


class _StubEP:
    """Minimal EntryPoint stand-in — only what _is_first_party / load() touch."""

    def __init__(self, name: str, value: str, dist_name: str, exc: Exception | None = None):
        self.name = name
        self.value = value
        self.dist = type("_Dist", (), {"name": dist_name})()
        self._exc = exc

    def load(self):
        if self._exc is not None:
            raise self._exc
        return object


def _stub_entry_points(monkeypatch, eps: list[_StubEP]) -> None:
    monkeypatch.setattr(_bootstrap.importlib.metadata, "entry_points", lambda **kw: list(eps))


def test_t10_stale_first_party_entry_point_fails_verify(monkeypatch):
    """A retired provider left in entry_points.txt is caught (the HATS-1115 gemini case)."""
    _stub_entry_points(
        monkeypatch,
        [
            _StubEP(
                "gemini",
                "ai_hats.providers:GeminiProvider",
                "ai-hats",
                exc=AttributeError("module 'ai_hats.providers' has no attribute 'GeminiProvider'"),
            )
        ],
    )

    failures = _bootstrap.find_integrity_failures()
    assert any("gemini" in f for f in failures), failures
    assert _bootstrap.verify_after_install() == 1


def test_t11_out_of_tree_provider_plugin_does_not_fail_verify(monkeypatch):
    """A third-party plugin must not fail the install verify (mirrors providers policy)."""
    _stub_entry_points(
        monkeypatch,
        [_StubEP("agy", "ai_hats_agy:AgyProvider", "ai-hats-agy", exc=ImportError("boom"))],
    )

    assert _bootstrap.find_integrity_failures() == []
    assert _bootstrap.verify_after_install() == 0


def test_t12_incoherent_own_module_fails_verify(monkeypatch):
    """An ai_hats module importing a symbol its sibling no longer exports."""
    _stub_entry_points(monkeypatch, [])
    real_import = _bootstrap.importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "ai_hats.assembler":
            raise ImportError("cannot import name 'PROVIDER_GEMINI' from 'ai_hats.constants'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(_bootstrap.importlib, "import_module", fake_import)

    failures = _bootstrap.find_integrity_failures()
    assert any("PROVIDER_GEMINI" in f for f in failures), failures
    assert _bootstrap.verify_after_install() == 1


def test_is_first_party_dist_metadata_fallback():
    """HATS-1118: _is_first_party falls back to dist.metadata.get('Name') when dist.name is missing."""

    class DummyMetadata:
        def get(self, key):
            return "ai-hats" if key == "Name" else None

    class DummyDist:
        name = None
        metadata = DummyMetadata()

    class DummyEP:
        dist = DummyDist()

    assert _bootstrap._is_first_party(DummyEP()) is True


def test_check_pycache_coherence_flags_stale_bytecode(tmp_path, monkeypatch):
    """HATS-1118: _check_pycache_coherence flags .pyc files with recorded mtime/size mismatch."""
    pkg_dir = tmp_path / "ai_hats"
    pkg_dir.mkdir()
    pycache_dir = pkg_dir / "__pycache__"
    pycache_dir.mkdir()

    source_py = pkg_dir / "sample.py"
    source_py.write_text("print('hello')\n")
    st = source_py.stat()
    real_mtime = int(st.st_mtime) & 0xFFFFFFFF
    real_size = st.st_size & 0xFFFFFFFF

    import struct

    # Create pyc with wrong recorded mtime
    wrong_header = struct.pack("<IIII", 0x0A0D0D03, 0, real_mtime - 10, real_size)
    pyc_file = pycache_dir / "sample.cpython-311.pyc"
    pyc_file.write_bytes(wrong_header + b"fakebytecode")

    class DummySpec:
        submodule_search_locations = [str(pkg_dir)]

    monkeypatch.setattr(
        _bootstrap.importlib.util,
        "find_spec",
        lambda name: DummySpec() if name == "ai_hats" else None,
    )

    failures = _bootstrap._check_pycache_coherence()
    assert len(failures) == 1
    assert "stale __pycache__" in failures[0]
