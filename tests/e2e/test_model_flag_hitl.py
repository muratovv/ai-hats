"""e2e (HATS-1891)

flow:   a developer picking a non-default model for an interactive session
cmds:
    ai-hats --dry-run-json -r test-role -m fable
    ai-hats execute --interactive --model fable -p nonexistent_provider_1891
    ai-hats execute --interactive --isolation squash -p nonexistent_provider_1891
expect: the alias reaches the provider argv as `--model fable` and the report still spawns
        nothing; `execute --interactive --model` gets past the batch-only guard and dies
        later, on the unknown provider; `--isolation` is still refused by that same guard
why:    `-m` used to reach the provider verbatim and die there ("unknown option '-m'"),
        and `execute --interactive --model` was refused with "the interactive runner
        cannot act on it" — a claim the pass-through argv had always disproved. The
        `--isolation` row is the positive control: without it, a guard that stopped
        refusing everything would read as a pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]


def _seed_role(project_path: Path) -> None:
    role_dir = project_path / "libraries" / "roles" / "test-role"
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\npriorities:\n  - Quality\ninjection: | \n  # Test Role\n"
    )


def test_e2e_model_alias_reaches_the_provider_argv(tmp_project):
    _seed_role(tmp_project.path)

    res = tmp_project.run("--dry-run-json", "-r", "test-role", "-m", "fable").expect_ok()
    launch = json.loads(res.stdout)["launch"]

    assert "--model" in launch, launch
    assert launch[launch.index("--model") + 1] == "fable"
    assert "-m" not in launch, "the alias must be translated, not forwarded verbatim"


def test_e2e_execute_interactive_accepts_model_but_still_refuses_isolation(tmp_project):
    _seed_role(tmp_project.path)

    accepted = tmp_project.run(
        "execute",
        "--role",
        "test-role",
        "--interactive",
        "--model",
        "fable",
        "-p",
        "nonexistent_provider_1891",
    ).expect_failure()
    combined = accepted.stdout + accepted.stderr
    assert "batch-only" not in combined, combined
    assert "nonexistent_provider_1891" in combined

    # Positive control: the guard this flag left is still standing.
    refused = tmp_project.run(
        "execute",
        "--role",
        "test-role",
        "--interactive",
        "--isolation",
        "squash",
        "-p",
        "nonexistent_provider_1891",
    ).expect_failure()
    assert "batch-only" in refused.stdout + refused.stderr
