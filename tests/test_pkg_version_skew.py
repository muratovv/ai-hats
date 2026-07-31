"""Unit tests for the workspace version-skew gate (HATS-943).

The RED baseline: `evaluate` must FAIL the exact HATS-937 shape — a package whose
`src/**` changed while its version still equals the published one.
"""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from pathlib import Path

import pytest
from packaging.version import Version

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_pkg_version_skew.py"
_spec = importlib.util.spec_from_file_location("check_pkg_version_skew", _SCRIPT)
skew = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass resolves cls.__module__ via sys.modules (py3.14).
sys.modules[_spec.name] = skew
_spec.loader.exec_module(skew)


class TestEvaluate:
    def test_src_changed_at_published_version_fails(self):
        # The HATS-937 skew: migrations added to core src, version still 0.3.0.
        v = skew.evaluate("ai_hats_core", Version("0.3.0"), Version("0.3.0"), src_changed=True)
        assert v.ok is False
        assert "stale wheel" in v.reason

    def test_src_changed_below_published_fails(self):
        v = skew.evaluate("ai_hats_core", Version("0.2.0"), Version("0.3.0"), src_changed=True)
        assert v.ok is False

    def test_bump_above_published_passes(self):
        # The HATS-937 fix: bumped to 0.4.0 over published 0.3.0.
        v = skew.evaluate("ai_hats_core", Version("0.4.0"), Version("0.3.0"), src_changed=True)
        assert v.ok is True

    def test_src_unchanged_never_requires_bump(self):
        v = skew.evaluate("ai_hats_core", Version("0.3.0"), Version("0.3.0"), src_changed=False)
        assert v.ok is True

    def test_never_published_passes(self):
        v = skew.evaluate("ai_hats_rack", Version("0.1.0"), None, src_changed=True)
        assert v.ok is True

    def test_dynamic_version_skipped(self):
        v = skew.evaluate("ai_hats", None, Version("0.13.0"), src_changed=True)
        assert v.ok is True
        assert "skipped" in v.reason


class TestSourceMeta:
    def test_static_version(self, tmp_path: Path):
        p = tmp_path / "pyproject.toml"
        p.write_text('[project]\nname = "ai-hats-core"\nversion = "0.4.0"\n')
        name, ver = skew.source_meta(p)
        assert name == "ai-hats-core"
        assert ver == Version("0.4.0")

    def test_dynamic_version_is_none(self, tmp_path: Path):
        p = tmp_path / "pyproject.toml"
        p.write_text('[project]\nname = "ai-hats"\ndynamic = ["version"]\n')
        name, ver = skew.source_meta(p)
        assert name == "ai-hats"
        assert ver is None


class TestLatestPypiVersion:
    def test_returns_info_version(self):
        payload = {"info": {"version": "0.3.0"}}
        assert skew.latest_pypi_version("ai-hats-core", fetch=lambda _u: payload) == Version(
            "0.3.0"
        )

    def test_404_is_unpublished(self):
        def fetch(_url):
            raise urllib.error.HTTPError(_url, 404, "not found", {}, None)

        assert skew.latest_pypi_version("brand-new-pkg", fetch=fetch) is None

    def test_non_404_http_error_propagates(self):
        def fetch(_url):
            raise urllib.error.HTTPError(_url, 503, "unavailable", {}, None)

        with pytest.raises(urllib.error.HTTPError):
            skew.latest_pypi_version("ai-hats-core", fetch=fetch)


class TestResolveBase:
    @pytest.fixture
    def git_repo(self, tmp_path: Path) -> Path:
        def _git(*args):
            import subprocess

            subprocess.run(
                ["git", *args],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                env={
                    "GIT_AUTHOR_NAME": "test",
                    "GIT_AUTHOR_EMAIL": "test@example.com",
                    "GIT_COMMITTER_NAME": "test",
                    "GIT_COMMITTER_EMAIL": "test@example.com",
                },
            )

        _git("init", "-b", "master")
        (tmp_path / "file.txt").write_text("hello")
        _git("add", "file.txt")
        _git("commit", "-m", "initial")
        return tmp_path

    def test_reachable_base_returns_ref(self, git_repo: Path):
        assert skew.resolve_base("HEAD", git_repo) == "HEAD"

    def test_unknown_sha_returns_none(self, git_repo: Path):
        assert skew.resolve_base("deadbeefdeadbeef", git_repo) is None

    def test_unrelated_history_returns_none(self, git_repo: Path):
        import subprocess

        env = {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        subprocess.run(
            ["git", "checkout", "--orphan", "unrelated"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            env=env,
        )
        (git_repo / "other.txt").write_text("unrelated")
        subprocess.run(
            ["git", "add", "other.txt"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            env=env,
        )
        subprocess.run(
            ["git", "commit", "-m", "unrelated commit"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            env=env,
        )

        assert skew.resolve_base("master", git_repo) is None


class TestMainUnusableBase:
    def test_unusable_base_prints_notice_and_exits_0(self, capsys):
        rc = skew.main(["deadbeefdeadbeef"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "[version-skew] base unusable" in captured.err
        assert "deadbeefdeadbeef" in captured.err
        assert "tests/test_package_version_drift.py" in captured.err
