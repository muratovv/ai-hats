"""HATS-764 Step 2 — pure channel resolver.

No network, no subprocess: ``resolve_channel`` is exercised with injected
facts (head_sha / latest_version / path) and the (version_id, install_spec,
mutable, editable) tuple asserted per channel.
"""

import json
import urllib.error

import pytest

from ai_hats.channel import (
    ChannelResolution,
    ChannelResolveError,
    fetch_edge_head_sha,
    fetch_latest_stable_version,
    resolve_channel,
    resolve_edge_repo,
)
from ai_hats.constants import ENV_REPO_URL
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
