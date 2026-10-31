"""e2e (HATS-2009)

flow:   a maintainer compares the two projections of one candidate plan
cmds:
    ai-hats -re dry-run "hypothesis-intake + worker" --full
    ai-hats -re dry-run "hypothesis-intake + worker" --json
    ai-hats -re show-prompt "hypothesis-intake + worker"
    ai-hats -re dry-run "safety-guard + worker" --json
expect: show-prompt prints the prompt body dry-run --full dumps; the text and json
        renderings carry one record; a processing warning lands in the notes of
        dry-run and on the stderr of show-prompt
why:    both projections read one plan value (ADR-0036 D5); this test grows with
        every slice so a later stage cannot make them disagree unnoticed
"""

from __future__ import annotations

import json
import re

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.library]

EXPR = "hypothesis-intake + worker"


def _prompt_body(full_text: str) -> str:
    """The prompt body ``--full`` prints between the prompt path and ``materialized``."""
    match = re.search(r"^prompt {4}\S+\n(.*)\n\nmaterialized\n", full_text, re.S | re.M)
    assert match, full_text[:400]
    return match.group(1)


def test_show_prompt_is_the_prompt_dry_run_dumps(tmp_project) -> None:
    full = tmp_project.run("-re", "dry-run", EXPR, "--full").expect_ok().stdout
    shown = tmp_project.run("-re", "show-prompt", EXPR).expect_ok().stdout
    body = _prompt_body(full)
    assert shown.rstrip("\n") in body, "the surface frames the context file around the same bytes"
    assert "## WORKER (paired session)" in shown


def test_text_and_json_are_one_record(tmp_project) -> None:
    text = tmp_project.run("-re", "dry-run", EXPR).expect_ok().stdout
    record = json.loads(tmp_project.run("-re", "dry-run", EXPR, "--json").expect_ok().stdout)
    assert text.startswith(f"role      {record['role']} -> {record['provider']}")
    assert (
        record["composition"]["digest"]
        == json.loads(tmp_project.run("-re", "dry-run", EXPR, "--json").expect_ok().stdout)[
            "composition"
        ]["digest"]
    ), "two plannings of one input are one plan"


def test_a_processing_warning_reaches_both_projections(tmp_project) -> None:
    expr = "safety-guard + worker"  # the first term brings no prose: body-first warning
    record = json.loads(tmp_project.run("-re", "dry-run", expr, "--json").expect_ok().stdout)
    assert any(
        "first body prompt, worker::prompt, comes through term 2" in n for n in record["notes"]
    )
    shown = tmp_project.run("-re", "show-prompt", expr).expect_ok()
    assert "WARN: the first body prompt, worker::prompt, comes through term 2" in shown.stderr
    assert shown.stdout.startswith("## WORKER"), "the warning does not withhold the prompt"
