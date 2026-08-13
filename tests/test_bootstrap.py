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
T13 bootstrap_or_die no-op heal: pip exits 0 but dep still missing → SystemExit(1), no execv (HATS-1359).
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


def _force_missing_until_healed(monkeypatch, name: str) -> dict:
    """Like _force_missing, but find_spec(name) flips to real once state["healed"] is set.

    Lets a test model an ACTUAL heal (the fake pip run really fixes the
    import) rather than a no-op — set state["healed"] = True from the fake
    subprocess.run.
    """
    state = {"healed": False}
    real_find = _bootstrap.importlib.util.find_spec

    def fake(name_, *a, **kw):
        if name_ == name and not state["healed"]:
            return None
        return real_find(name_, *a, **kw)

    monkeypatch.setattr(_bootstrap.importlib.util, "find_spec", fake)
    return state


def _fixed_requires(monkeypatch, reqs: list[str]) -> None:
    """Pin the METADATA snapshot. Detaches the editable path too (HATS-1368).

    The dev checkout is itself an editable install, so without this the live
    pyproject.toml would outrank the pinned list and these tests would assert
    against the repo's real dependency set. Tests that WANT the editable branch
    re-point ``_editable_source_dir`` after calling this.
    """
    monkeypatch.setattr(
        _bootstrap.importlib.metadata,
        "requires",
        lambda dist: list(reqs),
    )
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: None)


@pytest.fixture
def coherent_pycache(monkeypatch):
    """Detach ``verify_after_install`` from the developer's own checkout.

    ``_check_pycache_coherence`` resolves ``find_spec("ai_hats")`` to the real
    installed tree, so a stale ``.pyc`` there (another interpreter's bytecode, a
    regenerated ``_version.py``) fails tests that are about dependency healing
    (HATS-1201). The coherence contract has its own hermetic test below, against
    a synthetic tree.
    """
    monkeypatch.setattr(_bootstrap, "_check_pycache_coherence", lambda: [])


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
    """Missing dep → pip actually fixes the import → os.execv invoked with fresh interpreter."""
    state = _force_missing_until_healed(monkeypatch, "ptyprocess")
    # Wheel install: the by-name heal this test asserts. The editable branch
    # (HATS-1367) re-points the checkout instead and has its own tests.
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: None)

    pip_calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        pip_calls.append(list(cmd))
        state["healed"] = True
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


def test_t6_verify_after_install_success(monkeypatch, capsys, coherent_pycache):
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
    state = _force_missing_until_healed(monkeypatch, "ptyprocess")

    def fake_run(cmd, **kw):
        state["healed"] = True
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(_bootstrap.subprocess, "run", fake_run)
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


# ---------- T13 ----------


def test_t13_bootstrap_or_die_noop_heal_fails_loud(monkeypatch, capsys):
    """HATS-1359: pip exits 0 but the dep is still missing → SystemExit(1), no execv.

    Models the empirically-confirmed no-op-heal shape (stale dist-info, the
    import stays broken) instead of the pre-fix behaviour of re-exec'ing
    forever into the identical missing-dep state.
    """
    _force_missing(monkeypatch, {"ptyprocess"})  # never actually gets fixed

    monkeypatch.setattr(
        _bootstrap.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0})(),  # uv "succeeds" — a no-op
    )

    def boom_execv(*a, **kw):
        raise AssertionError("execv must NOT be called when the dep is still missing")

    monkeypatch.setattr(_bootstrap.os, "execv", boom_execv)

    with pytest.raises(SystemExit) as exc:
        _bootstrap.bootstrap_or_die()
    assert exc.value.code == 1

    err = capsys.readouterr().err
    assert "ptyprocess" in err
    assert "uv reported success" in err


# ---------- T9 ----------


def test_t9_future_dep_cycle(monkeypatch, coherent_pycache):
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


def test_t11_out_of_tree_provider_plugin_does_not_fail_verify(monkeypatch, coherent_pycache):
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


def test_check_pycache_coherence_auto_heals_stale_bytecode(tmp_path, monkeypatch):
    """HATS-1234: _check_pycache_coherence unlinks stale .pyc files (auto-heal) and returns no failure."""
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
    assert failures == []
    assert not pyc_file.exists()


def test_check_pycache_coherence_reports_failure_when_unlink_fails(tmp_path, monkeypatch):
    """HATS-1234: _check_pycache_coherence reports failure when stale .pyc cannot be unlinked."""
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

    def failing_unlink(path, *, dir_fd=None):
        # Signature-compatible with os.unlink: the patch below is process-wide,
        # so a narrower stub raises TypeError inside unrelated callers (rmtree).
        raise OSError("Permission denied")

    monkeypatch.setattr(_bootstrap.os, "unlink", failing_unlink)

    failures = _bootstrap._check_pycache_coherence()
    assert len(failures) == 1
    assert "stale __pycache__" in failures[0]


# ---------- T14: editable source detection (HATS-1368/HATS-1367) ----------


def _direct_url(monkeypatch, payload: str | None) -> None:
    """Stub the PEP 610 direct_url.json the active ai-hats install carries."""

    class _Dist:
        def read_text(self, name):
            assert name == "direct_url.json"
            return payload

    monkeypatch.setattr(_bootstrap.importlib.metadata, "distribution", lambda _: _Dist())


def test_t14_editable_source_dir_returns_checkout(monkeypatch, tmp_path):
    """dir_info.editable + an on-disk file:// url → that path."""
    _direct_url(monkeypatch, f'{{"url": "file://{tmp_path}", "dir_info": {{"editable": true}}}}')
    assert _bootstrap._editable_source_dir() == str(tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            '{"url": "file:///src/ai-hats", "dir_info": {}}',
            id="local-non-editable",  # what `uv pip install <dir>` really writes
        ),
        pytest.param(
            '{"url": "https://example/ai_hats.whl", "archive_info": {}}', id="direct-url-wheel"
        ),
        pytest.param('{"url": "git+ssh://git@host/ai-hats", "vcs_info": {"vcs": "git"}}', id="vcs"),
    ],
)
def test_t14b_editable_source_dir_none_for_non_editable(monkeypatch, payload):
    """A non-editable install has nothing to re-point at, whatever wrote its metadata.

    Verified against real installs (HATS-1368 review): `uv pip install <dir>`
    writes ``dir_info: {}`` — no ``editable`` key — and a plain index install
    writes no direct_url.json at all (covered by the None case in T14d).
    """
    _direct_url(monkeypatch, payload)
    assert _bootstrap._editable_source_dir() is None


def test_t14c_editable_source_dir_none_when_path_gone(monkeypatch, tmp_path):
    """The checkout was moved or deleted → no path worth printing."""
    gone = tmp_path / "gone"
    _direct_url(monkeypatch, f'{{"url": "file://{gone}", "dir_info": {{"editable": true}}}}')
    assert _bootstrap._editable_source_dir() is None


@pytest.mark.parametrize("payload", [None, "", "{not json", '{"dir_info": {"editable": true}}'])
def test_t14d_editable_source_dir_fails_open(monkeypatch, payload):
    """Missing, empty, malformed, or url-less metadata never crashes bootstrap."""
    _direct_url(monkeypatch, payload)
    assert _bootstrap._editable_source_dir() is None


def test_t14e_editable_source_dir_none_when_not_installed(monkeypatch):
    """No ai-hats distribution at all (running from a source tree) → None."""

    def boom(_):
        raise _bootstrap.importlib.metadata.PackageNotFoundError("ai-hats")

    monkeypatch.setattr(_bootstrap.importlib.metadata, "distribution", boom)
    assert _bootstrap._editable_source_dir() is None


# ---------- T15: the live pyproject outranks frozen METADATA (HATS-1368) ----------


def _editable_checkout(monkeypatch, tmp_path, deps: list[str] | None, *, body=None) -> None:
    """Point _editable_source_dir at tmp_path and give it a pyproject with ``deps``."""
    if body is None:
        rendered = ", ".join(f'"{d}"' for d in (deps or []))
        body = f'[project]\nname = "ai-hats"\ndependencies = [{rendered}]\n'
    (tmp_path / "pyproject.toml").write_text(body)
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: str(tmp_path))


def test_t15_underdeclared_metadata_no_longer_hides_a_dep(monkeypatch, tmp_path):
    """Class B: METADATA predates the workspace split; the live pyproject exposes the gap."""
    _fixed_requires(monkeypatch, ["click>=8.1"])  # 0.8.x-era snapshot: no first-party deps
    _editable_checkout(monkeypatch, tmp_path, ["click>=8.1", "ai-hats-wt>=0.4.2"])
    _force_missing(monkeypatch, {"ai_hats_wt"})

    assert "ai-hats-wt" in _bootstrap.find_missing_runtime_deps()


def test_t15b_overdeclared_metadata_no_longer_invents_a_dep(monkeypatch, tmp_path):
    """Class A: METADATA still names a deleted workspace member; the live pyproject doesn't."""
    _fixed_requires(monkeypatch, ["click>=8.1", "ai-hats-tracker>=0.6.1"])
    _editable_checkout(monkeypatch, tmp_path, ["click>=8.1"])
    _force_missing(monkeypatch, {"ai_hats_tracker"})

    assert _bootstrap.find_missing_runtime_deps() == []


def test_t15c_wheel_install_still_reads_metadata(monkeypatch, tmp_path):
    """No editable checkout → METADATA is authoritative and stays the source."""
    _fixed_requires(monkeypatch, ["ai-hats-wt>=0.4.2"])
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "ai-hats"\ndependencies = []\n')
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: None)
    _force_missing(monkeypatch, {"ai_hats_wt"})

    assert _bootstrap.find_missing_runtime_deps() == ["ai-hats-wt"]


@pytest.mark.parametrize(
    "body",
    ["not = [toml", '[project]\nname = "ai-hats"\n', "[build-system]\nrequires = []\n"],
    ids=["malformed", "no-dependencies", "no-project-table"],
)
def test_t15d_unreadable_pyproject_falls_back_to_metadata(monkeypatch, tmp_path, body):
    """Fail-open: an unusable checkout must never leave the gate with no source at all."""
    _fixed_requires(monkeypatch, ["ai-hats-wt>=0.4.2"])
    _editable_checkout(monkeypatch, tmp_path, None, body=body)
    _force_missing(monkeypatch, {"ai_hats_wt"})

    assert _bootstrap.find_missing_runtime_deps() == ["ai-hats-wt"]


def test_t15e_missing_pyproject_falls_back_to_metadata(monkeypatch, tmp_path):
    """The checkout exists but carries no pyproject.toml at all."""
    _fixed_requires(monkeypatch, ["ai-hats-wt>=0.4.2"])
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: str(tmp_path))
    _force_missing(monkeypatch, {"ai_hats_wt"})

    assert _bootstrap.find_missing_runtime_deps() == ["ai-hats-wt"]


# ---------- T16: the rescue command must actually repair the venv (HATS-1367) ----------


def test_t16_rescue_command_reinstalls_the_editable_checkout(monkeypatch):
    """Naming the dist is a no-op on an editable install — re-point the checkout instead."""
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: "/src/ai-hats")

    cmd = _bootstrap._rescue_command(["ai-hats-wt"])

    assert cmd == f"uv pip install --python {sys.executable} -e '/src/ai-hats'"


def test_t16b_rescue_command_unchanged_for_wheel_install(monkeypatch):
    """A wheel install has no checkout to re-point — install the dists by name."""
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: None)

    cmd = _bootstrap._rescue_command(["ptyprocess", "click"])

    assert cmd == f"uv pip install --python {sys.executable} 'ptyprocess' 'click'"


def test_t16c_self_heal_reinstalls_the_editable_checkout(monkeypatch):
    """The command bootstrap RUNS branches with the one it PRINTS — same repair, one source."""
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: "/src/ai-hats")
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(_bootstrap.subprocess, "run", fake_run)

    assert _bootstrap.attempt_self_heal(["ai-hats-wt"]) is True
    assert calls == [["uv", "pip", "install", "--python", sys.executable, "-e", "/src/ai-hats"]]


def test_t16d_self_heal_unchanged_for_wheel_install(monkeypatch):
    """Non-editable keeps the pre-HATS-1367 by-name install."""
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: None)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(_bootstrap.subprocess, "run", fake_run)

    assert _bootstrap.attempt_self_heal(["ptyprocess"]) is True
    assert calls == [["uv", "pip", "install", "--python", sys.executable, "ptyprocess"]]


# ---------- T17: the broken-install notice must not advise a dead end (HATS-1368) ----------


def test_t17_repair_command_repoints_an_editable_checkout(monkeypatch):
    """`self update` cannot run when the import that broke is the CLI it lives in."""
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: "/src/ai-hats")

    assert _bootstrap.repair_command() == (
        f"uv pip install --python {sys.executable} -e '/src/ai-hats'"
    )


def test_t17b_repair_command_keeps_self_update_for_wheel(monkeypatch):
    """A wheel install has no checkout to re-point — `self update` is the repair."""
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: None)

    assert _bootstrap.repair_command() == "python -m ai_hats self update (or 'ai-hats self update')"


# ---------- T18: a .pth heal is invisible until the site hook re-runs (HATS-1368) ----------


@pytest.mark.parametrize(
    ("returncode", "expected"), [(0, True), (1, False)], ids=["success", "failure"]
)
def test_t18_self_heal_refreshes_import_paths_only_on_success(monkeypatch, returncode, expected):
    """Without the refresh the HATS-1359 recheck reads an editable heal as a no-op."""
    monkeypatch.setattr(_bootstrap, "_editable_source_dir", lambda: None)
    monkeypatch.setattr(
        _bootstrap.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": returncode})(),
    )
    refreshed: list[bool] = []
    monkeypatch.setattr(_bootstrap, "_refresh_import_paths", lambda: refreshed.append(True))

    assert _bootstrap.attempt_self_heal(["ptyprocess"]) is expected
    assert bool(refreshed) is expected


def test_t18b_refresh_survives_an_unusable_site_dir(monkeypatch):
    """A refresh that cannot run must not take the heal down with it."""

    def boom(_):
        raise OSError("no such directory")

    monkeypatch.setattr(_bootstrap.importlib, "invalidate_caches", lambda: None)
    import site

    monkeypatch.setattr(site, "addsitedir", boom)

    _bootstrap._refresh_import_paths()  # must not raise
