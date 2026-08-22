"""HATS-1599 — the ratchet that refuses more patching of code under test.

The gate counts, per test file, the calls that replace something *inside* the
unit being tested. `setenv` is deliberately absent from that count: an entry
point whose job is to read the environment is tested by setting it, and the
rule pushes ambient reads up to exactly such a point.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_test_isolation.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_test_isolation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load()


def count(source: str) -> int:
    return mod.patch_calls(source)


# --- what counts as reaching into the unit ---------------------------------


def test_setattr_on_the_module_under_test_counts():
    assert count("def test_x(monkeypatch):\n    monkeypatch.setattr(mod, 'run', fake)\n") == 1


def test_a_string_target_counts_the_same():
    source = "def test_x(monkeypatch):\n    monkeypatch.setattr('pkg.mod.decide', lambda: 1)\n"

    assert count(source) == 1


def test_every_call_in_a_file_is_counted():
    source = (
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(mod, 'a', 1)\n"
        "    monkeypatch.delattr(mod, 'b')\n"
        "    monkeypatch.setitem(sys.modules, 'anthropic', None)\n"
    )

    assert count(source) == 3


def test_chdir_counts_because_the_unit_should_have_been_told():
    assert count("def test_x(monkeypatch):\n    monkeypatch.chdir(tmp_path)\n") == 1


def test_a_renamed_monkeypatch_fixture_still_counts():
    assert count("def test_x(mp):\n    mp.setattr(mod, 'run', fake)\n") == 1


# --- the env-contract exemption --------------------------------------------


def test_setenv_is_not_counted():
    source = "def test_x(monkeypatch):\n    monkeypatch.setenv('HOME', str(tmp_path))\n"

    assert count(source) == 0


def test_delenv_is_not_counted():
    assert count("def test_x(monkeypatch):\n    monkeypatch.delenv('CI', raising=False)\n") == 0


def test_undo_is_not_a_patch():
    assert count("def test_x(monkeypatch):\n    monkeypatch.undo()\n") == 0


# --- the same defect spelled with unittest.mock ------------------------------


def test_a_patch_decorator_counts():
    source = (
        "from unittest.mock import patch\n\n@patch('pkg.mod.decide')\ndef test_x(m):\n    pass\n"
    )

    assert count(source) == 1


def test_patch_object_counts():
    source = (
        "from unittest.mock import patch\n\n"
        "def test_x():\n    with patch.object(mod, 'run'):\n        pass\n"
    )

    assert count(source) == 1


def test_patch_through_the_mock_module_counts():
    source = "from unittest import mock\n\ndef test_x():\n    with mock.patch('pkg.mod.run'):\n        pass\n"

    assert count(source) == 1


def test_an_aliased_patch_import_counts():
    source = "from unittest.mock import patch as p\n\n@p('pkg.mod.run')\ndef test_x(m):\n    pass\n"

    assert count(source) == 1


def test_an_unrelated_patch_method_is_not_counted():
    """An HTTP client's `.patch()` shares the name and nothing else."""
    source = "def test_x(client):\n    client.patch('/items/1', json={})\n"

    assert count(source) == 0


def test_a_local_helper_named_patch_is_not_counted():
    source = "def patch(x):\n    return x\n\ndef test_x():\n    patch(1)\n"

    assert count(source) == 0


# --- the ratchet ------------------------------------------------------------


def compare(measured: dict[str, int], baseline: dict[str, int]) -> list[str]:
    return [v.path for v in mod.compare(measured, baseline)]


def test_a_count_at_the_baseline_is_clean():
    assert compare({"tests/a.py": 3}, {"tests/a.py": 3}) == []


def test_a_count_above_the_baseline_is_a_violation():
    assert compare({"tests/a.py": 4}, {"tests/a.py": 3}) == ["tests/a.py"]


def test_a_file_absent_from_the_baseline_is_a_violation():
    assert compare({"tests/new.py": 1}, {}) == ["tests/new.py"]


def test_a_file_that_patches_nothing_needs_no_baseline_entry():
    assert compare({}, {}) == []


def test_a_count_below_the_baseline_is_a_violation_too():
    """Unclaimed slack is a future free rise — the ratchet has to be re-cut."""
    assert compare({"tests/a.py": 1}, {"tests/a.py": 3}) == ["tests/a.py"]


def test_a_baseline_entry_for_a_file_that_no_longer_patches_is_a_violation():
    assert compare({}, {"tests/a.py": 3}) == ["tests/a.py"]


def test_a_rise_names_the_file_and_both_numbers():
    """One line per file — what to do about it prints once, as a footer."""
    (violation,) = mod.compare({"tests/a.py": 4}, {"tests/a.py": 3})

    assert "tests/a.py" in str(violation)
    assert "4" in str(violation) and "3" in str(violation)


def test_a_drop_says_to_re_record_it():
    (violation,) = mod.compare({"tests/a.py": 1}, {"tests/a.py": 3})

    assert "--update" in str(violation)


def test_violations_are_reported_in_path_order():
    measured = {"tests/b.py": 1, "tests/a.py": 1}

    assert compare(measured, {}) == ["tests/a.py", "tests/b.py"]


# --- discovery --------------------------------------------------------------

PATCHES_ONCE = "def test_x(monkeypatch):\n    monkeypatch.setattr(mod, 'run', fake)\n"


def fake_repo(root: Path, tree: dict[str, str], baseline: dict[str, int] | None = None) -> Path:
    for rel, source in tree.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    mod.write_baseline(mod.baseline_path(root), baseline or {})
    return root


def test_scan_reaches_every_test_tree_in_the_workspace(tmp_path):
    root = fake_repo(
        tmp_path,
        {
            "tests/a.py": PATCHES_ONCE,
            "packages/ai-hats-rack/tests/b.py": PATCHES_ONCE,
            "packages/surfaces/agy/tests/c.py": PATCHES_ONCE,
            # An area keeps its tests inside the package (ADR-0026 D5).
            "src/ai_hats/pipeline/tests/d.py": PATCHES_ONCE,
        },
    )

    assert mod.scan(root) == {
        "packages/ai-hats-rack/tests/b.py": 1,
        "packages/surfaces/agy/tests/c.py": 1,
        "src/ai_hats/pipeline/tests/d.py": 1,
        "tests/a.py": 1,
    }


def test_scan_ignores_production_trees(tmp_path):
    root = fake_repo(tmp_path, {"src/ai_hats/thing.py": PATCHES_ONCE})

    assert mod.scan(root) == {}


def test_a_file_that_patches_nothing_is_left_out_of_the_scan(tmp_path):
    root = fake_repo(tmp_path, {"tests/clean.py": "def test_x():\n    assert 1\n"})

    assert mod.scan(root) == {}


# --- the entry point --------------------------------------------------------


def test_a_tree_matching_its_baseline_exits_zero(tmp_path):
    root = fake_repo(tmp_path, {"tests/a.py": PATCHES_ONCE}, baseline={"tests/a.py": 1})

    assert mod.main([str(root)]) == 0


def test_a_rise_fails_and_names_the_file(tmp_path, capsys):
    root = fake_repo(tmp_path, {"tests/a.py": PATCHES_ONCE}, baseline={})

    assert mod.main([str(root)]) == 1
    assert "tests/a.py" in capsys.readouterr().err


def test_the_three_exits_print_once_for_the_whole_run(tmp_path, capsys):
    """The gate is the only delivery point for this decision, so the exits
    travel with the failure — but 20 offending files must not print them 20x."""
    tree = {"tests/a.py": PATCHES_ONCE, "tests/b.py": PATCHES_ONCE}
    root = fake_repo(tmp_path, tree, baseline={})

    mod.main([str(root)])
    err = capsys.readouterr().err

    assert err.count("third-party boundary") == 1
    assert err.count("INJECT") == 1 and err.count("EXEMPT") == 1 and err.count("RECORD") == 1
    assert err.count("tests/a.py") == 1 and err.count("tests/b.py") == 1


def test_a_green_run_does_not_lecture(tmp_path, capsys):
    root = fake_repo(tmp_path, {"tests/a.py": PATCHES_ONCE}, baseline={"tests/a.py": 1})

    mod.main([str(root)])

    assert "third-party boundary" not in capsys.readouterr().err


def test_update_refuses_to_record_a_rise(tmp_path):
    root = fake_repo(tmp_path, {"tests/a.py": PATCHES_ONCE}, baseline={})

    assert mod.main([str(root), "--update"]) == 2
    assert mod.load_baseline(mod.baseline_path(root)) == {}


def test_update_re_cuts_the_ratchet_after_a_drop(tmp_path):
    root = fake_repo(tmp_path, {"tests/a.py": PATCHES_ONCE}, baseline={"tests/a.py": 5})

    assert mod.main([str(root), "--update"]) == 0
    assert mod.load_baseline(mod.baseline_path(root)) == {"tests/a.py": 1}


def test_update_drops_a_file_that_stopped_patching(tmp_path):
    root = fake_repo(tmp_path, {"tests/a.py": "def test_x():\n    assert 1\n"}, {"tests/a.py": 2})

    assert mod.main([str(root), "--update"]) == 0
    assert mod.load_baseline(mod.baseline_path(root)) == {}


# --- the real repo ----------------------------------------------------------


def test_this_repo_matches_its_recorded_baseline():
    measured = mod.scan(REPO_ROOT)
    baseline = mod.load_baseline(mod.baseline_path(REPO_ROOT))

    assert not mod.compare(measured, baseline), "\n".join(
        str(v) for v in mod.compare(measured, baseline)
    )
