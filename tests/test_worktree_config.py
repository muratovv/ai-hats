"""HATS-942 Step 1 — worktree base/merge-target schema (WorktreeConfig).

Pure schema slice: absent-block default, explicit load, base-only load,
unknown-nested-key forward-compat WARN, and omit-when-default byte-clean
round-trips. Mirrors ``test_harness_config.py`` (the sibling nested sub-model).
"""

import yaml

from ai_hats.models import ProjectConfig, WorktreeConfig
from ai_hats.paths import PROJECT_CONFIG

BASE = "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: agy\n"


def _write(tmp_path, body: str):
    p = tmp_path / PROJECT_CONFIG
    p.write_text(body)
    return p


def test_absent_worktree_block_defaults_empty(tmp_path):
    cfg = ProjectConfig.from_yaml(_write(tmp_path, BASE))
    assert cfg.worktree.is_default
    assert cfg.worktree.base_branch is None
    assert cfg.worktree.merge_target is None


def test_explicit_base_and_target_load(tmp_path):
    cfg = ProjectConfig.from_yaml(
        _write(tmp_path, BASE + "worktree:\n  base_branch: main\n  merge_target: fork-main\n")
    )
    assert cfg.worktree.base_branch == "main"
    assert cfg.worktree.merge_target == "fork-main"


def test_base_only_loads_target_none(tmp_path):
    cfg = ProjectConfig.from_yaml(_write(tmp_path, BASE + "worktree:\n  base_branch: trunk\n"))
    assert cfg.worktree.base_branch == "trunk"
    assert cfg.worktree.merge_target is None


def test_unknown_nested_worktree_key_warns_but_loads(tmp_path, capsys):
    # Forward-compat: a newer ai-hats may add a nested worktree field; an older
    # binary DROPS it (no crash) but WARNs so the vanished field is observable.
    cfg = ProjectConfig.from_yaml(
        _write(tmp_path, BASE + "worktree:\n  merge_target: fork-main\n  strategy: rebase\n")
    )
    assert cfg.worktree.merge_target == "fork-main"
    assert not hasattr(cfg.worktree, "strategy")
    assert "dropping unknown field 'strategy'" in capsys.readouterr().err


def test_default_worktree_omitted_from_to_dict():
    assert "worktree" not in ProjectConfig().to_dict()


def test_non_default_worktree_serialized():
    cfg = ProjectConfig(worktree=WorktreeConfig(base_branch="main", merge_target="fork-main"))
    assert cfg.to_dict()["worktree"] == {"base_branch": "main", "merge_target": "fork-main"}


def test_worktree_omits_none_fields():
    cfg = ProjectConfig(worktree=WorktreeConfig(merge_target="fork-main"))
    assert cfg.to_dict()["worktree"] == {"merge_target": "fork-main"}


def test_roundtrip_byte_clean_with_block(tmp_path):
    cfg = ProjectConfig(worktree=WorktreeConfig(base_branch="main", merge_target="fork-main"))
    p = tmp_path / PROJECT_CONFIG
    cfg.save(p)
    first = p.read_text()
    reloaded = ProjectConfig.from_yaml(p)
    reloaded.save(p)
    assert p.read_text() == first
    assert reloaded.worktree == cfg.worktree
    assert yaml.safe_load(first)["worktree"] == {"base_branch": "main", "merge_target": "fork-main"}


def test_back_compat_no_worktree_stays_byte_clean(tmp_path):
    # An ai-hats.yaml with NO worktree block loads empty AND must save byte-clean
    # (no spurious `worktree:`). Locks the default so a future change cannot
    # silently regress back-compat.
    p = _write(tmp_path, BASE)
    cfg = ProjectConfig.from_yaml(p)
    cfg.save(p)
    assert "worktree" not in p.read_text()
    assert cfg.worktree.is_default
