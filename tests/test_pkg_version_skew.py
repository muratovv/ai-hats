"""Unit tests for the workspace version-skew gate (HATS-943).

The RED baseline: `evaluate` must FAIL the exact HATS-937 shape — a package whose
`src/**` changed while its version still equals the published one — and must NOT
fail its twin (HATS-1957), where the version equals the published one because the
bump in this very diff is what got published.
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

    def test_bump_published_by_the_same_push_passes(self):
        # HATS-1957, run 34464727909: master auto-publishes a landed bump, so
        # equality with PyPI is the healthy END state — strict `>` made the
        # verdict a race with release-packages.yml.
        v = skew.evaluate(
            "ai_hats_core",
            Version("0.12.0"),
            Version("0.12.0"),
            src_changed=True,
            base_ver=Version("0.11.0"),
        )
        assert v.ok is True
        assert "0.11.0" in v.reason

    def test_no_bump_in_the_diff_still_fails(self):
        # The control on the case above: run 34135274201, ai-hats-library left
        # at 0.6.5 across the diff while 0.6.5 was published. Still the skew.
        v = skew.evaluate(
            "ai_hats_library",
            Version("0.6.5"),
            Version("0.6.5"),
            src_changed=True,
            base_ver=Version("0.6.5"),
        )
        assert v.ok is False
        assert "stale wheel" in v.reason

    def test_behind_pypi_fails_even_when_bumped(self):
        v = skew.evaluate(
            "ai_hats_core",
            Version("0.12.0"),
            Version("0.13.0"),
            src_changed=True,
            base_ver=Version("0.11.0"),
        )
        assert v.ok is False
        assert "behind" in v.reason


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


GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def git(repo: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(  # noqa: S603, S607 — fixed argv, no shell
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=GIT_ENV
    ).stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "master")
    (tmp_path / "file.txt").write_text("hello")
    git(tmp_path, "add", "file.txt")
    git(tmp_path, "commit", "-m", "initial")
    return tmp_path


class TestVersionAt:
    """The offline half of the verdict, over a real git object store."""

    def _pyproject(self, version: str) -> str:
        return f'[project]\nname = "ai-hats-core"\nversion = "{version}"\n'

    @pytest.fixture
    def repo_with_bump(self, git_repo: Path) -> tuple[Path, str]:
        """A repo whose HEAD bumped ai-hats-core 0.11.0 -> 0.12.0. Returns (repo, base)."""
        pkg = git_repo / "packages" / "ai-hats-core"
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text(self._pyproject("0.11.0"))
        git(git_repo, "add", "packages")
        git(git_repo, "commit", "-m", "core at 0.11.0")
        base = git(git_repo, "rev-parse", "HEAD")
        (pkg / "pyproject.toml").write_text(self._pyproject("0.12.0"))
        git(git_repo, "commit", "-am", "bump core to 0.12.0")
        return git_repo, base

    def test_reads_the_version_the_base_commit_carried(self, repo_with_bump):
        repo, base = repo_with_bump
        assert skew.version_at(base, "packages/ai-hats-core/pyproject.toml", repo) == Version(
            "0.11.0"
        )

    def test_head_carries_the_bumped_version(self, repo_with_bump):
        repo, _ = repo_with_bump
        assert skew.version_at("HEAD", "packages/ai-hats-core/pyproject.toml", repo) == Version(
            "0.12.0"
        )

    def test_absent_at_base_is_none(self, repo_with_bump):
        # A package this very diff added has no base version to compare against.
        repo, base = repo_with_bump
        assert skew.version_at(base, "packages/ai-hats-brand-new/pyproject.toml", repo) is None

    def test_dynamic_version_at_base_is_none(self, git_repo: Path):
        pkg = git_repo / "packages" / "ai-hats"
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text('[project]\nname = "ai-hats"\ndynamic = ["version"]\n')
        git(git_repo, "add", "packages")
        git(git_repo, "commit", "-m", "dynamic version")
        assert skew.version_at("HEAD", "packages/ai-hats/pyproject.toml", git_repo) is None


class TestResolveBase:
    def test_reachable_base_returns_the_merge_base_commit(self, git_repo: Path):
        head = git(git_repo, "rev-parse", "HEAD")
        assert skew.resolve_base("HEAD", git_repo) == head

    def test_unknown_sha_returns_none(self, git_repo: Path):
        assert skew.resolve_base("deadbeefdeadbeef", git_repo) is None

    def test_unrelated_history_returns_none(self, git_repo: Path):
        git(git_repo, "checkout", "--orphan", "unrelated")
        (git_repo / "other.txt").write_text("unrelated")
        git(git_repo, "add", "other.txt")
        git(git_repo, "commit", "-m", "unrelated commit")

        assert skew.resolve_base("master", git_repo) is None


class TestMainUnusableBase:
    def test_unusable_base_prints_notice_and_exits_0(self, capsys):
        rc = skew.main(["deadbeefdeadbeef"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "[version-skew] base unusable" in captured.err
        assert "deadbeefdeadbeef" in captured.err
        assert "tests/test_package_version_drift.py" in captured.err
