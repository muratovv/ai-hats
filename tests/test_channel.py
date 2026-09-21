"""HATS-764 Step 2 — pure channel resolver.

No network, no subprocess: ``resolve_channel`` is exercised with injected
facts (head_sha / latest_version / path) and the (version_id, install_spec,
mutable, editable) tuple asserted per channel.
"""

import json
import sys
import urllib.error

import pytest

from ai_hats.channel import (
    EdgeSource,
    EditableSource,
    LocalSource,
    detect_editable_source,
    editable_source_from_url,
    resolve_edge_source,
    resolve_local_source,
    source_problem,
    ChannelResolution,
    ChannelResolveError,
    fetch_edge_head_sha,
    fetch_latest_stable_version,
    resolve_channel,
    resolve_edge_probe_url,
    resolve_edge_repo,
)
from ai_hats.constants import ENV_AI_HATS_INIT_SRC, ENV_REPO_URL
from ai_hats.models import Channel


def test_local_editable_in_place():
    r = resolve_channel(Channel.LOCAL, path="/work/ai-hats")
    assert r == ChannelResolution(
        channel=Channel.LOCAL,
        version_id=None,
        install_spec="/work/ai-hats",
        mutable=True,
        editable=True,
    )


def test_local_defaults_path_to_cwd():
    r = resolve_channel(Channel.LOCAL)
    assert r.install_spec == "."
    assert r.editable is True and r.version_id is None


def test_edge_git_url_pins_sha():
    r = resolve_channel(
        Channel.EDGE,
        repo="git+https://github.com/acme/ai-hats.git",
        head_sha="1a2b3c4",
    )
    assert r.version_id == "1a2b3c4"
    assert r.install_spec == "ai-hats @ git+https://github.com/acme/ai-hats.git@1a2b3c4"
    assert r.mutable is True and r.editable is False


def test_edge_local_path_repo_uses_bare_path():
    # The e2e harness points AI_HATS_REPO_URL at a local checkout (no scheme):
    # pip builds the working tree; the version dir is still keyed by the sha.
    r = resolve_channel(Channel.EDGE, repo="/tmp/checkout", head_sha="deadbee")
    assert r.install_spec == "/tmp/checkout"
    assert r.version_id == "deadbee"


def test_stable_pins_version_immutable():
    r = resolve_channel(Channel.STABLE, latest_version="0.8.1")
    assert r == ChannelResolution(
        channel=Channel.STABLE,
        version_id="0.8.1",
        install_spec="ai-hats==0.8.1",
        mutable=False,
        editable=False,
    )


def test_stable_version_id_keys_a_tag_shaped_dir():
    # version_id keying accepts a tag (dots) just like an edge sha.
    r = resolve_channel(Channel.STABLE, latest_version="v0.8.0")
    assert r.version_id == "v0.8.0"


def test_edge_missing_repo_raises():
    with pytest.raises(ValueError):
        resolve_channel(Channel.EDGE, head_sha="abc")


def test_edge_missing_sha_raises():
    with pytest.raises(ValueError):
        resolve_channel(Channel.EDGE, repo="git+https://x/y.git")


def test_stable_missing_version_raises():
    with pytest.raises(ValueError):
        resolve_channel(Channel.STABLE)


# ---------- Step 3: effectful fetchers (stubbed — no network) ----------


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_resolve_edge_repo_default_upstream(monkeypatch):
    monkeypatch.delenv(ENV_REPO_URL, raising=False)
    assert resolve_edge_repo() == "git+https://github.com/muratovv/ai-hats.git"


def test_resolve_edge_repo_coerces_ssh_yaml_repo(monkeypatch):
    monkeypatch.delenv(ENV_REPO_URL, raising=False)
    assert (
        resolve_edge_repo("git@github.com:acme/ai-hats.git")
        == "git+https://github.com/acme/ai-hats.git"
    )


def test_resolve_edge_repo_env_beats_yaml(monkeypatch):
    monkeypatch.setenv(ENV_REPO_URL, "https://github.com/env/ai-hats.git")
    assert (
        resolve_edge_repo("https://github.com/yaml/ai-hats.git")
        == "git+https://github.com/env/ai-hats.git"
    )


def test_resolve_edge_repo_local_path_stays_bare(monkeypatch):
    monkeypatch.setenv(ENV_REPO_URL, "/tmp/checkout")
    assert resolve_edge_repo() == "/tmp/checkout"


# ---------- resolve_edge_probe_url + relocated primitives (HATS-987) ----------


def test_resolve_edge_probe_url_bare_https_default(monkeypatch):
    # Probe URL is the bare-https sibling of resolve_edge_repo (no git+ prefix —
    # `git ls-remote` needs none).
    monkeypatch.delenv(ENV_REPO_URL, raising=False)
    assert resolve_edge_probe_url() == "https://github.com/muratovv/ai-hats.git"


def test_resolve_edge_probe_url_env_beats_yaml_and_coerces(monkeypatch):
    monkeypatch.setenv(ENV_REPO_URL, "git+ssh://git@github.com/env/ai-hats.git")
    assert (
        resolve_edge_probe_url("https://github.com/yaml/ai-hats.git")
        == "https://github.com/env/ai-hats.git"
    )


def test_probe_and_repo_share_precedence(monkeypatch):
    # Probe (bare) and install-spec (git+) differ only by the git+ prefix — both
    # resolve from the same env>yaml>fallback source.
    monkeypatch.delenv(ENV_REPO_URL, raising=False)
    yaml_repo = "git@github.com:acme/ai-hats.git"
    assert resolve_edge_probe_url(yaml_repo) == "https://github.com/acme/ai-hats.git"
    assert resolve_edge_repo(yaml_repo) == "git+https://github.com/acme/ai-hats.git"


def test_url_primitives_reexported_by_checker():
    # checker re-exports the channel-homed primitives (back-compat + banner path).
    from ai_hats import channel
    from ai_hats.update_check import checker

    assert channel.FALLBACK_REMOTE_URL is checker.FALLBACK_REMOTE_URL
    assert channel._coerce_to_https is checker._coerce_to_https


def test_edge_resolution_independent_of_update_check(monkeypatch):
    # Relocate fail-under-revert: edge URL resolution must work with update_check
    # absent (the packaging-regression scenario). Reverted code re-introduces a
    # lazy `from .update_check.checker import …` here → ImportError under the stub.
    monkeypatch.setitem(sys.modules, "ai_hats.update_check.checker", None)
    monkeypatch.delenv(ENV_REPO_URL, raising=False)
    assert resolve_edge_probe_url("git@github.com:a/b.git") == "https://github.com/a/b.git"
    assert resolve_edge_repo("git@github.com:a/b.git") == "git+https://github.com/a/b.git"


def test_fetch_edge_head_sha_delegates_to_resolve_ref(monkeypatch):
    monkeypatch.setattr(
        "ai_hats.cli.maintenance._resolve_ref",
        lambda url, ref: "cafe123" if ref == "HEAD" else None,
    )
    assert fetch_edge_head_sha("git+https://x/y.git") == "cafe123"


def test_fetch_latest_stable_version_success(monkeypatch):
    body = json.dumps({"info": {"version": "0.8.1"}}).encode()
    monkeypatch.setattr(
        "ai_hats.channel.urllib.request.urlopen",
        lambda req, timeout=10: _FakeResp(body),
    )
    assert fetch_latest_stable_version() == "0.8.1"


def test_fetch_latest_stable_version_unreachable_fails_loud(monkeypatch):
    # PyPI offline / not-yet-published (the 764 reality) → fail LOUD, never
    # silently fall back to edge. Live stable path is HATS-765.
    def boom(req, timeout=10):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("ai_hats.channel.urllib.request.urlopen", boom)
    with pytest.raises(ChannelResolveError):
        fetch_latest_stable_version()


def test_fetch_latest_stable_version_no_version_field(monkeypatch):
    monkeypatch.setattr(
        "ai_hats.channel.urllib.request.urlopen",
        lambda req, timeout=10: _FakeResp(json.dumps({"info": {}}).encode()),
    )
    with pytest.raises(ChannelResolveError):
        fetch_latest_stable_version()


# ---------- source identity ----------


def _ai_hats_source(root, name="ai-hats"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "0"\n')
    return root


def test_source_problem_is_none_for_a_pyproject_naming_ai_hats(tmp_path):
    assert source_problem(_ai_hats_source(tmp_path / "src")) is None


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda p: p, "does not exist"),
        (lambda p: (p.write_text(""), p)[1], "is not a directory"),
        (lambda p: (p.mkdir(), p)[1], "has no pyproject.toml"),
        (lambda p: _ai_hats_source(p, name="consumer"), "names 'consumer', not ai-hats"),
        (
            lambda p: (p.mkdir(), (p / "pyproject.toml").write_text("= broken"), p)[2],
            "pyproject.toml is unreadable",
        ),
        (
            lambda p: (p.mkdir(), (p / "pyproject.toml").write_text("[tool.x]\n"), p)[2],
            "names nothing",
        ),
    ],
    ids=["missing", "file", "no-pyproject", "other-project", "unreadable", "nameless"],
)
def test_source_problem_names_why_a_dir_is_not_ai_hats(tmp_path, build, reason):
    path = build(tmp_path / "x")
    problem = source_problem(path)
    assert problem is not None and str(path) in problem and reason in problem, problem


def test_setup_py_alone_is_not_an_ai_hats_source(tmp_path):
    """An installable dir is not the same thing as the ai-hats source (review F1)."""
    root = tmp_path / "x"
    root.mkdir()
    (root / "setup.py").write_text("")
    assert source_problem(root) is not None


# ---------- editable-source detection ----------


def test_detect_editable_source_prefers_the_launcher_export(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_AI_HATS_INIT_SRC, str(tmp_path))
    assert detect_editable_source() == EditableSource(
        path=str(tmp_path), origin=ENV_AI_HATS_INIT_SRC
    )


def test_editable_source_from_url_decodes_the_pep610_url():
    """uv percent-encodes the file url; an undecoded path never exists (review F3)."""
    found = editable_source_from_url("file:///Users/dev/ai-hats%20with%20space")
    assert found == EditableSource(
        path="/Users/dev/ai-hats with space", origin="the editable install this process runs from"
    )


def test_editable_source_from_url_is_none_for_a_non_file_url():
    assert editable_source_from_url("https://github.com/x/ai-hats.git") is None
    assert editable_source_from_url(None) is None


# ---------- local source resolution ----------


def _detected(path, origin="the editable install this process runs from"):
    return EditableSource(path=str(path), origin=origin)


def test_local_source_explicit_absolute_path_wins(tmp_path):
    checkout = _ai_hats_source(tmp_path / "checkout")
    src = resolve_local_source(tmp_path / "proj", str(checkout), detected=_detected(tmp_path / "o"))
    assert src == LocalSource(path=checkout, origin="harness.path", problem=None)


def test_local_source_relative_path_is_against_the_project_root(tmp_path):
    project = tmp_path / "proj"
    _ai_hats_source(project / "vendor" / "ai-hats")
    src = resolve_local_source(project, "vendor/ai-hats", detected=None)
    assert src.path == project / "vendor" / "ai-hats"
    assert src.problem is None


def test_local_source_expands_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _ai_hats_source(tmp_path / "ai-hats")
    src = resolve_local_source(tmp_path / "proj", "~/ai-hats", detected=None)
    assert src.path == tmp_path / "ai-hats"


def test_local_source_detected_editable_beats_the_project_root(tmp_path):
    detected = _ai_hats_source(tmp_path / "dev" / "ai-hats")
    project = _ai_hats_source(tmp_path / "proj")  # even an ai-hats root loses to detection
    src = resolve_local_source(project, None, detected=_detected(detected))
    assert src == LocalSource(
        path=detected, origin="the editable install this process runs from", problem=None
    )


def test_local_source_defaults_to_an_ai_hats_project_root(tmp_path):
    project = _ai_hats_source(tmp_path / "proj")
    src = resolve_local_source(project, None, detected=None)
    assert src == LocalSource(path=project, origin="the project root", problem=None)


def test_local_source_names_the_problem_and_origin_for_a_bare_root(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    src = resolve_local_source(project, None, detected=None)
    assert src.path == project and src.origin == "the project root"
    assert src.problem is not None and str(project) in src.problem
    assert src.fix == "ai-hats config set --channel local --path <checkout>"


def test_local_source_a_consumer_project_root_is_a_problem(tmp_path):
    """The root has a pyproject — of the consumer, not ai-hats (review F1/M1)."""
    project = _ai_hats_source(tmp_path / "proj", name="consumer")
    src = resolve_local_source(project, None, detected=None)
    assert src.problem is not None and "names 'consumer'" in src.problem


def test_local_source_explicit_path_that_is_missing_is_a_problem(tmp_path):
    missing = tmp_path / "gone"
    src = resolve_local_source(tmp_path / "proj", str(missing), detected=None)
    assert src.path == missing and src.origin == "harness.path"
    assert src.problem is not None and "does not exist" in src.problem


def test_local_source_fix_names_the_env_var_when_it_chose_the_path(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    src = resolve_local_source(
        tmp_path / "proj", None, detected=_detected(empty, ENV_AI_HATS_INIT_SRC)
    )
    assert src.origin == ENV_AI_HATS_INIT_SRC
    assert src.problem is not None
    assert ENV_AI_HATS_INIT_SRC in src.fix and "config set" not in src.fix


# ---------- edge source resolution ----------


def test_edge_source_git_url_has_no_offline_problem(monkeypatch):
    monkeypatch.delenv(ENV_REPO_URL, raising=False)
    src = resolve_edge_source("https://example.test/ai-hats.git")
    assert src == EdgeSource(
        spec="git+https://example.test/ai-hats.git", origin="harness.repo", problem=None
    )


def test_edge_source_local_path_is_judged_by_identity(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_REPO_URL, raising=False)
    ok = _ai_hats_source(tmp_path / "checkout")
    assert resolve_edge_source(str(ok)).problem is None
    notpy = tmp_path / "gitrepo-notpy"
    notpy.mkdir()
    bad = resolve_edge_source(str(notpy))
    assert bad.problem is not None and "has no pyproject.toml" in bad.problem
    assert bad.fix == "ai-hats config set --channel edge --repo <git-url-or-checkout>"


def test_edge_source_missing_path_is_not_offline(monkeypatch):
    monkeypatch.delenv(ENV_REPO_URL, raising=False)
    src = resolve_edge_source("/nonexistent/ai-hats-repo")
    assert src.problem is not None and "does not exist" in src.problem


def test_edge_source_env_override_names_itself_in_the_fix(tmp_path, monkeypatch):
    notpy = tmp_path / "notpy"
    notpy.mkdir()
    monkeypatch.setenv(ENV_REPO_URL, str(notpy))
    src = resolve_edge_source("https://example.test/ai-hats.git")
    assert src.origin == ENV_REPO_URL
    assert src.problem is not None
    assert ENV_REPO_URL in src.fix
