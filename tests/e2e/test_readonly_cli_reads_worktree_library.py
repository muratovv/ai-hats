"""e2e (HATS-1911)

flow:   someone authors a role inside a linked worktree and asks the ordinary
        read-only commands about it from that worktree, expecting their own tree
        to be the one reported
cmds:
    git worktree add --detach <wt>
    python -m ai_hats list roles | list traits | list tokens <role> | config status
expect: each command reports the component that exists ONLY in the worktree —
        `list tokens` in particular must print a budget rather than
        "Role '...' not found", which is what it printed before this
why:    the failure was silent and plausible: a missing role reads as broken
        YAML and a foreign tree's budget looks exactly like your own. It needs a
        real subprocess for the reason HATS-1501 documents — in process
        `_detect_source_library_root(cwd)` already returns the worktree, so an
        in-process probe agrees with the fix while the shipped CLI still reads
        master. `AI_HATS_LIBRARY_ROOT` is deliberately unset: setting it is the
        manual workaround this test exists to remove
"""
# comment-length: allow — the four-field catalog block, schema in gen_e2e_catalog.py

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

from _helpers.env import checkout_pythonpath
from _helpers.git import git

LIB = Path("packages/ai-hats-library/src/ai_hats_library")
ROLE = "zz-worktree-only-role"
TRAIT = "zz-worktree-only-trait"
RULE = "zz-worktree-only-rule"


def _bare_env(repo_root: Path) -> dict[str, str]:
    """Child env with the checkout importable but NO library-root override."""
    env = {**os.environ}
    env["PYTHONPATH"] = checkout_pythonpath(repo_root)
    for leaked in ("AI_HATS_LIBRARY_ROOT", "AI_HATS_PROJECT_DIR", "AI_HATS_DIR"):
        env.pop(leaked, None)
    return env


def _plant_worktree_only_components(wt: Path) -> None:
    """A role + trait + rule that exist in the WORKTREE's library and nowhere else."""
    lib = wt / LIB
    (lib / "usage" / "rules" / RULE).mkdir(parents=True)
    (lib / "usage" / "rules" / RULE / "rule.md").write_text(f"# Rule: {RULE}\n\nSentinel body.\n")
    (lib / "usage" / "traits" / TRAIT).mkdir(parents=True)
    (lib / "usage" / "traits" / TRAIT / "config.yaml").write_text(
        f"name: {TRAIT}\nrules:\n  - {RULE}\nskills: []\ninjection: |\n  ## {TRAIT}\n"
    )
    (lib / "usage" / "roles" / ROLE).mkdir(parents=True)
    (lib / "usage" / "roles" / ROLE / "config.yaml").write_text(
        f"name: {ROLE}\npriorities:\n  - Measurement\n"
        f"composition:\n  traits:\n    - {TRAIT}\n  rules: []\n  skills: []\n"
        f"injection: |\n  # ROLE: {ROLE}\n"
    )


def _run(wt: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ai_hats", *args],
        cwd=str(wt),
        env=_bare_env(wt),
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.integration
def test_readonly_commands_report_the_worktree_library(repo_root: Path, tmp_path: Path):
    main = tmp_path / "main"
    git(tmp_path, "clone", "--local", str(repo_root), str(main))
    # MAIN must be ONBOARDED, or the project hop refuses it and the worktree
    # resolves as its own project — which passes with or without the fix.
    ProjectConfig(provider="claude").save(main / PROJECT_CONFIG)
    Assembler(main).init()

    wt = tmp_path / "wt-1911"
    git(main, "worktree", "add", "--detach", str(wt))
    _plant_worktree_only_components(wt)

    # Only now: `init` composes to WRITE, so it resolves main's library, where
    # this role deliberately does not exist. `config status` reads the role name
    # from the project and the role itself from the library — the split under test.
    cfg = ProjectConfig.from_yaml(main / PROJECT_CONFIG)
    cfg.active_role = ROLE
    cfg.save(main / PROJECT_CONFIG)

    # The precondition that makes this test discriminating, asserted rather than
    # assumed: the project IS main, so anything keying off it reads main's tree.
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ai_hats.cli._entry import resolve_project; print(resolve_project().layout.root)",
        ],
        cwd=str(wt),
        env=_bare_env(wt),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == str(main), (
        f"project resolved to {probe.stdout.strip()}, not main — the hop did not "
        "fire, so this test would pass without the fix"
    )

    roles = _run(wt, "list", "roles")
    assert roles.returncode == 0, roles.stderr
    assert ROLE in roles.stdout, "`list roles` reported the MAIN checkout's library"

    traits = _run(wt, "list", "traits")
    assert traits.returncode == 0, traits.stderr
    assert TRAIT in traits.stdout, "`list traits` reported the MAIN checkout's library"

    rules = _run(wt, "list", "rules")
    assert rules.returncode == 0, rules.stderr
    assert RULE in rules.stdout, "`list rules` reported the MAIN checkout's library"

    tokens = _run(wt, "list", "tokens", ROLE)
    assert tokens.returncode == 0, tokens.stderr
    # The reported symptom, asserted on content: the failure printed
    # "Error: Role '<name>' not found" at exit 0, and that text CONTAINS the
    # role name — so only the trait row distinguishes the two outcomes.
    assert "not found" not in tokens.stdout, tokens.stdout
    assert TRAIT in tokens.stdout, "`list tokens` composed the MAIN checkout's library — HATS-1911"

    status = _run(wt, "config", "status")
    assert status.returncode == 0, status.stderr
    # The tree lists what the ROLE declares, so the trait is the worktree-only
    # node to look for here — the rule reaches the composition through the trait
    # and never appears as a branch of its own.
    assert TRAIT in status.stdout, f"`config status`'s role tree came from MAIN\n{status.stdout}"
