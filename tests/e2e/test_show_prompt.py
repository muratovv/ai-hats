"""E2E: ``ai-hats config show-prompt`` materializes role prompts on demand.

HATS-452 Phase 1. The CLI command is the user-facing surface for
"what would the agent actually see for role X". Verifies both text and
``--stats`` modes against a temp project pointed at this repo's
``library/``.

Per ``dev_rule_e2e_gate``: change touches ``src/ai_hats/cli/`` —
e2e required. Mocking is limited to ``bootstrap_or_die`` (network /
self-update probe); the rest is real composition + real provider
rendering.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIB_CORE = REPO_ROOT / "library" / "core"
LIB_USAGE = REPO_ROOT / "library" / "usage"


# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke]


@pytest.fixture
def project_with_maintainer(tmp_path: Path, monkeypatch) -> Path:
    """Tmp project pointing at this repo's library layers (core + usage)
    so the real composition is exercised, including the new
    ``rule_composition_value_contract`` rule wired into ``trait-agent``.
    """
    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIB_CORE), str(LIB_USAGE)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[LIB_CORE, LIB_USAGE])
    asm.init()
    asm.set_role("maintainer", provider_name="claude")
    monkeypatch.chdir(project)
    # bootstrap_or_die does a self-update probe — stub for offline / CI.
    import ai_hats._bootstrap as boot
    monkeypatch.setattr(boot, "bootstrap_or_die", lambda: None)
    return project


def test_show_prompt_text_mode_active_role(project_with_maintainer):
    """``ai-hats config show-prompt`` with no flags prints the active
    role's full system prompt, containing trait + role injection bodies
    (HATS-452 regression marker)."""
    res = CliRunner().invoke(main, ["config", "show-prompt"])
    assert res.exit_code == 0, f"exit={res.exit_code}, output:\n{res.output}"
    out = res.output
    # Section header invariants.
    assert "## PRIORITIES" in out
    assert "## RULES" in out
    # HATS-701: claude provider suppresses the AVAILABLE SKILLS index —
    # show-prompt mirrors the real prompt (skills reach the agent via the
    # native --plugin-dir registry, not the system prompt).
    assert "## AVAILABLE SKILLS" not in out
    # Injection bodies between PRIORITIES and RULES (the HATS-452 fix).
    assert "E2E gate" in out, "ai-hats-maintainer trait injection missing"
    assert "Agent Protocol" in out, "trait-agent injection missing"
    assert "primary development assistant for the" in out, (
        "role maintainer's own injection missing"
    )
    # Layout: injection sits in the PRIORITIES..RULES band.
    pri = out.find("## PRIORITIES")
    rules = out.find("## RULES")
    e2e = out.find("E2E gate")
    assert 0 <= pri < e2e < rules


def test_show_prompt_explicit_role(project_with_maintainer):
    """``--role`` switches composition without touching active_role."""
    res = CliRunner().invoke(main, ["config", "show-prompt", "--role", "assistant"])
    assert res.exit_code == 0, f"exit={res.exit_code}, output:\n{res.output}"
    out = res.output
    # Assistant role doesn't have the maintainer-specific E2E gate, but
    # DOES carry trait-agent's Agent Protocol.
    assert "Agent Protocol" in out
    assert "E2E gate" not in out, "assistant should not carry maintainer-specific markers"


def test_show_prompt_stats_mode_emits_json(project_with_maintainer):
    """``--stats`` emits JSON with composition counts; first key in the
    object is ``role`` (alphabetic insertion-order from MaterializeSystemPrompt)."""
    res = CliRunner().invoke(main, ["config", "show-prompt", "--stats"])
    assert res.exit_code == 0, f"exit={res.exit_code}, output:\n{res.output}"
    payload = json.loads(res.output)
    assert payload["role"] == "maintainer"
    assert payload["provider"] == "claude"
    assert payload["trait_count"] >= 5  # at minimum trait-base/agent/etc
    assert payload["injection_chars"] > 1000  # the bug produced 0 here
    assert payload["prompt_chars"] > payload["injection_chars"]
    # New rule wired in via Phase 3.
    assert "rule_composition_value_contract" in payload.get(
        "trait_names", []
    ) or payload.get("rule_count", 0) >= 10
