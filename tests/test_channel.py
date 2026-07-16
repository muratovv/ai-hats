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
    ChannelResolution,
    ChannelResolveError,
    fetch_edge_head_sha,
    fetch_latest_stable_version,
    resolve_channel,
    resolve_edge_probe_url,
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
