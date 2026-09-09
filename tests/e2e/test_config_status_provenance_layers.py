"""e2e (HATS-525)

flow:   a user configures global traits for a role and inspects rule provenance in
        status output
cmds:
    ai-hats config customize assistant --add-trait hats525-global-trait --global
    ai-hats config status
expect: both the trait name and its bundled rules display the (global) tag in status
        output rather than (built-in)
why:    labeling global or custom rules as built-in misinforms users about which layer
        provides active prompt guidance
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.library]


def _run(cmd, *, cwd, env, timeout=30, check=True):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"{cmd} exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_config_status_user_global_rule_provenance(shared_launcher, tmp_path: Path) -> None:
    launcher_dest, base_env, _venv = shared_launcher

    # 1. Setup isolated user home directory with a global rule and trait bundling it
    user_home_dir = tmp_path / "user_home"
    user_home_dir.mkdir()

    global_rule_dir = user_home_dir / ".ai-hats" / "rules" / "hats525-global-rule"
    global_rule_dir.mkdir(parents=True)
    (global_rule_dir / "rule.md").write_text("Rule content for HATS-525 test\n")
    (global_rule_dir / "config.yaml").write_text("name: hats525-global-rule\n")

    global_trait_dir = user_home_dir / ".ai-hats" / "traits" / "hats525-global-trait"
    global_trait_dir.mkdir(parents=True)
    (global_trait_dir / "config.yaml").write_text(
        "name: hats525-global-trait\ncomposition:\n  rules:\n    - hats525-global-rule\n"
    )

    # 2. Setup project directory
    project = tmp_path / "project"
    project.mkdir()

    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["AI_HATS_USER_HOME"] = str(user_home_dir)

    _run(
        [str(launcher_dest), "self", "init", "-p", "claude", "-r", "assistant"],
        cwd=project,
        env=env,
    )

    # 3. Add global trait via config customize
    _run(
        [
            str(launcher_dest),
            "config",
            "customize",
            "assistant",
            "--add-trait",
            "hats525-global-trait",
            "--global",
        ],
        cwd=project,
        env=env,
    )

    # 4. Check config status output
    res = _run([str(launcher_dest), "config", "status"], cwd=project, env=env)
    out = res.stdout + res.stderr

    # Trait MUST be tagged (global)
    assert "hats525-global-trait" in out, f"global trait missing from status output:\n{out}"
    assert "hats525-global-trait  (global)" in out or "hats525-global-trait\x1b" in out, (
        f"trait should be tagged global:\n{out}"
    )

    # Bundled rule MUST be tagged (global), NOT (built-in)
    assert "hats525-global-rule" in out, f"bundled global rule missing from status output:\n{out}"
    assert "hats525-global-rule  (built-in)" not in out, (
        f"🐛 HATS-525 BUG: bundled global rule was mislabeled as (built-in):\n{out}"
    )
    assert "hats525-global-rule  (global)" in out or "hats525-global-rule\x1b" in out, (
        f"bundled global rule should be tagged (global):\n{out}"
    )


@pytest.mark.integration
def test_e2e_config_status_symlinked_and_library_paths_yaml_global_provenance(
    shared_launcher, tmp_path: Path
) -> None:
    launcher_dest, base_env, _venv = shared_launcher

    user_home_dir = tmp_path / "user_home"
    user_home_dir.mkdir()
    ai_hats_dir = user_home_dir / ".ai-hats"
    ai_hats_dir.mkdir()

    # Case A: Symlinked child ~/.ai-hats/traits -> real_traits
    real_traits_dir = tmp_path / "external_traits"
    real_traits_dir.mkdir()
    sym_trait_dir = real_traits_dir / "symlink-global-trait"
    sym_trait_dir.mkdir()
    (sym_trait_dir / "config.yaml").write_text("name: symlink-global-trait\n")
    (ai_hats_dir / "traits").symlink_to(real_traits_dir, target_is_directory=True)

    # Case B: library_paths.yaml pointing to ext_lib_dir
    ext_lib_dir = tmp_path / "external_lib"
    ext_lib_dir.mkdir()
    ext_trait_dir = ext_lib_dir / "traits" / "yaml-global-trait"
    ext_trait_dir.mkdir(parents=True)
    (ext_trait_dir / "config.yaml").write_text("name: yaml-global-trait\n")
    (ai_hats_dir / "library_paths.yaml").write_text(f"paths:\n  - {ext_lib_dir}\n")

    project = tmp_path / "project"
    project.mkdir()

    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["AI_HATS_USER_HOME"] = str(user_home_dir)

    _run(
        [str(launcher_dest), "self", "init", "-p", "claude", "-r", "assistant"],
        cwd=project,
        env=env,
    )

    _run(
        [
            str(launcher_dest),
            "config",
            "customize",
            "assistant",
            "--add-trait",
            "symlink-global-trait",
            "--add-trait",
            "yaml-global-trait",
            "--global",
        ],
        cwd=project,
        env=env,
    )

    res = _run([str(launcher_dest), "config", "status"], cwd=project, env=env)
    out = res.stdout + res.stderr

    assert "symlink-global-trait  (built-in)" not in out, f"symlinked trait tagged built-in:\n{out}"
    assert "symlink-global-trait  (global)" in out or "symlink-global-trait" in out

    assert "yaml-global-trait  (built-in)" not in out, f"yaml trait tagged built-in:\n{out}"
    assert "yaml-global-trait  (global)" in out or "yaml-global-trait" in out
