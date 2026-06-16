"""HATS-764 Step 2 — pure channel resolver.

No network, no subprocess: ``resolve_channel`` is exercised with injected
facts (head_sha / latest_version / path) and the (version_id, install_spec,
mutable, editable) tuple asserted per channel.
"""

import pytest

from ai_hats.channel import ChannelResolution, resolve_channel
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
