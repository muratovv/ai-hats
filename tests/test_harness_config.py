"""HATS-764 Step 1 — harness-source schema (Channel + HarnessConfig).

Pure schema slice: default-stable back-compat, explicit-channel load,
unknown-channel fail-loud, and omit-when-default byte-clean round-trips.
"""

import pytest
import yaml

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


# -- HATS-792: forward-safe config reader (preserve + fail-loud) --


def test_unknown_top_level_field_survives_read_write_read(tmp_path, capsys):
    """HATS-792 (a): a same-version unknown TOP-LEVEL key is PRESERVED across
    read→write→read (round-trip), instead of being dropped on the next save.

    Fail-under-revert: drop the ``_extra`` capture in ``from_yaml`` or the merge
    in ``to_dict`` → the field vanishes from the rewritten yaml and the reload
    below loses it.
    """
    p = _write(tmp_path, BASE + "future_field: keep-me\n")

    cfg = ProjectConfig.from_yaml(p)
    # HATS-581 (d): the WARN must still fire even though we now preserve.
    assert "dropping unknown field 'future_field'" in capsys.readouterr().err
    # The unknown key is NOT a typed attribute (extra="forbid" still holds).
    assert not hasattr(cfg, "future_field")

    cfg.save(p)
    on_disk = yaml.safe_load(p.read_text())
    assert on_disk["future_field"] == "keep-me"

    # Reload: the field is still there (full round-trip).
    reloaded = ProjectConfig.from_yaml(p)
    assert reloaded.to_dict()["future_field"] == "keep-me"


def test_unknown_field_absent_when_none_present(tmp_path):
    """A clean config (no unknown keys) gains NO spurious _extra output —
    ``to_dict`` stays byte-clean. Guards against _extra leaking known keys."""
    p = _write(tmp_path, BASE)
    cfg = ProjectConfig.from_yaml(p)
    assert cfg._extra == {}
    # No unexpected top-level keys beyond the known serialized set.
    assert "future_field" not in cfg.to_dict()


def test_newer_schema_version_fails_loud(tmp_path):
    """HATS-792 (b): schema_version newer than KNOWN_SCHEMA_VERSION (4) is
    refused with a typed error + remediation, not silently treated as v4.

    Fail-under-revert: remove the from_yaml guard → schema_version 5 loads
    silently and this raise-assertion fails.
    """
    p = _write(tmp_path, "schema_version: 5\nai_hats_dir: .agent/ai-hats\nprovider: gemini\n")

    with pytest.raises(ProjectConfigError) as exc:
        ProjectConfig.from_yaml(p)
    msg = str(exc.value)
    assert "schema_version 5 is newer" in msg
    assert "ai-hats self update" in msg


def test_save_refuses_to_clobber_newer_schema_on_disk(tmp_path):
    """HATS-792: an old binary must not overwrite a future config it cannot
    represent — even on a bypass path that constructs a config WITHOUT loading
    the existing (future) file first."""
    p = _write(tmp_path, "schema_version: 99\nai_hats_dir: .agent/ai-hats\nprovider: gemini\n")
    before = p.read_text()

    with pytest.raises(ProjectConfigError) as exc:
        ProjectConfig(provider="claude").save(p)
    assert "refusing to overwrite" in str(exc.value)
    # The future file is byte-for-byte untouched.
    assert p.read_text() == before


def test_harness_still_round_trips_byte_clean_with_extra_seam(tmp_path):
    """HATS-764 guard under HATS-792: an explicit harness block still round-trips
    byte-clean, and the default harness stays omitted, with the _extra seam in
    place (a clean config carries no extras, so nothing changes)."""
    cfg = ProjectConfig(harness=HarnessConfig(channel=Channel.EDGE, repo="https://x/y.git"))
    p = tmp_path / "ai-hats.yaml"
    cfg.save(p)
    first = p.read_text()
    reloaded = ProjectConfig.from_yaml(p)
    reloaded.save(p)
    assert p.read_text() == first
    assert reloaded.harness == cfg.harness
    # Default harness still omitted under the new seam.
    assert "harness" not in ProjectConfig().to_dict()
