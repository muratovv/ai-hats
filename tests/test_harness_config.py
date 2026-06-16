"""HATS-764 Step 1 — harness-source schema (Channel + HarnessConfig).

Pure schema slice: default-stable back-compat, explicit-channel load,
unknown-channel fail-loud, and omit-when-default byte-clean round-trips.
"""

import pytest

from ai_hats.models import (
    Channel,
    HarnessConfig,
    ProjectConfig,
    ProjectConfigError,
)

# Minimal valid v4 ai-hats.yaml prelude (ai_hats_dir required, provider valid).
BASE = "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: gemini\n"


def _write(tmp_path, body: str):
    p = tmp_path / "ai-hats.yaml"
    p.write_text(body)
    return p


def test_absent_harness_block_defaults_stable(tmp_path):
    cfg = ProjectConfig.from_yaml(_write(tmp_path, BASE))
    assert cfg.harness.channel is Channel.STABLE
    assert cfg.harness.is_default


@pytest.mark.parametrize(
    "name,chan",
    [("local", Channel.LOCAL), ("edge", Channel.EDGE), ("stable", Channel.STABLE)],
)
def test_explicit_channels_load(tmp_path, name, chan):
    cfg = ProjectConfig.from_yaml(_write(tmp_path, BASE + f"harness:\n  channel: {name}\n"))
    assert cfg.harness.channel is chan


def test_unknown_channel_fails_loud(tmp_path):
    with pytest.raises(ProjectConfigError):
        ProjectConfig.from_yaml(_write(tmp_path, BASE + "harness:\n  channel: bogus\n"))


def test_unknown_nested_harness_key_warns_but_loads(tmp_path, capsys):
    # Forward-compat: a newer ai-hats may add a nested harness field; an older
    # binary DROPS it (no crash) but WARNs so the vanished field is observable.
    cfg = ProjectConfig.from_yaml(
        _write(tmp_path, BASE + "harness:\n  channel: edge\n  branch: main\n")
    )
    assert cfg.harness.channel is Channel.EDGE
    assert not hasattr(cfg.harness, "branch")
    assert "dropping unknown field 'branch'" in capsys.readouterr().err


def test_default_harness_omitted_from_to_dict():
    assert "harness" not in ProjectConfig().to_dict()


def test_non_default_harness_serialized():
    cfg = ProjectConfig(harness=HarnessConfig(channel=Channel.EDGE, repo="https://x/y.git"))
    assert cfg.to_dict()["harness"] == {"channel": "edge", "repo": "https://x/y.git"}


def test_harness_omits_none_repo_path():
    cfg = ProjectConfig(harness=HarnessConfig(channel=Channel.LOCAL))
    assert cfg.to_dict()["harness"] == {"channel": "local"}


def test_local_path_serialized():
    cfg = ProjectConfig(harness=HarnessConfig(channel=Channel.LOCAL, path="."))
    assert cfg.to_dict()["harness"] == {"channel": "local", "path": "."}


def test_roundtrip_byte_clean_with_block(tmp_path):
    cfg = ProjectConfig(harness=HarnessConfig(channel=Channel.EDGE, repo="https://x/y.git"))
    p = tmp_path / "ai-hats.yaml"
    cfg.save(p)
    first = p.read_text()
    reloaded = ProjectConfig.from_yaml(p)
    reloaded.save(p)
    assert p.read_text() == first
    assert reloaded.harness == cfg.harness


def test_back_compat_no_harness_stays_byte_clean(tmp_path):
    # An ai-hats.yaml with NO harness block loads as stable AND must save
    # byte-clean (no spurious `harness:`). Locks the default so a future
    # default change cannot silently regress back-compat.
    p = _write(tmp_path, BASE)
    cfg = ProjectConfig.from_yaml(p)
    cfg.save(p)
    assert "harness" not in p.read_text()
    assert cfg.harness.channel is Channel.STABLE
