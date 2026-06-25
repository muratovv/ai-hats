"""E2E regression: HATS-452 — composed role/trait injection MUST reach the
agent's system prompt.

Triggering bug. ``ai-hats`` (bare, no ``--role``, active_role=maintainer)
wrote a ``prompt.md`` containing PRIORITIES + RULES + AVAILABLE SKILLS but
**no merged_injection** — hundreds of lines of role/trait behavioral
guidance never reached the agent. Root cause: ``compose_role`` pipeline
step returned ``{"system_prompt": ""}`` for missing role; runtime's
``if system_prompt_override is not None:`` accepted empty string and
**replaced** the freshly-composed injections list with ``[""]``.

Scope of this test. Drives the full ``human`` pipeline (the bare-``ai-hats``
path) end-to-end with the real maintainer role from this repo's
``library/``. The only mock is ``WrapRunner._pty_spawn`` — we capture the
``--system-prompt-file`` argv the wrapper would have handed to
``claude`` and inspect the file *in situ* before per-session cache
cleanup. Composition, file write, and the funnel/override flow all run
for real.

Fail-under-revert (per ``dev_rule_e2e_gate`` §4). Reverting any of:
- ``pipeline/steps/compose.py`` (empty-string-as-absent)
- ``pipeline/pipeline.py`` (None-normalization at funnel merge)
- ``runtime.py`` (drop of ``system_prompt_override`` from ``WrapRunner``)

…makes ``test_bare_session_includes_full_role_injection`` fail — the
prompt loses the unique markers (``E2E gate``, ``Agent Protocol``,
``primary development assistant for the``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.models import ProjectConfig


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "library"


# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke]


# --------------------------------------------------------------------- #
# Fixture: tmp project with maintainer as the active role
# --------------------------------------------------------------------- #


@pytest.fixture
def project_with_maintainer_default(tmp_path: Path, monkeypatch) -> Path:
    """Fresh tmp project mirroring the production layout: this repo's real
    ``library/`` is wired in as a library path, ``active_role=maintainer``
    so a bare ``ai-hats`` invocation triggers exactly the HATS-452 code
    path (role=None at CLI, resolved to maintainer downstream).
    """
    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(project / "ai-hats.yaml")
    asm = Assembler(project, library_paths=[LIBRARY_DIR])
    asm.init()
    asm.set_role("maintainer", provider_name="claude")
    monkeypatch.chdir(project)
    return project


# --------------------------------------------------------------------- #
# Capture helper: monkey-patch _pty_spawn to snapshot the prompt file
# --------------------------------------------------------------------- #


def _install_pty_capture(monkeypatch, sink: dict[str, Any]) -> None:
    """Replace ``WrapRunner._pty_spawn`` with a stub that captures the
    ``--system-prompt-file`` argument's content into ``sink``, then returns 0
    so the wrapper's finally-block (cache cleanup, session finalization)
    runs as in production. We read the file BEFORE returning so that the
    snapshot survives ``_cleanup_session_cache``.
    """
    from ai_hats import runtime as rt

    def _capture(_self, cmd, env, tracer):  # noqa: ARG001 (mirror real sig)
        sink["cmd"] = list(cmd)
        # Find --system-prompt-file <path> in argv and read content NOW.
        for i, tok in enumerate(cmd):
            if tok == "--system-prompt-file" and i + 1 < len(cmd):
                p = Path(cmd[i + 1])
                if p.exists():
                    sink["prompt_text"] = p.read_text()
                else:
                    sink["prompt_text"] = None
                sink["prompt_path"] = str(p)
                break
        return 0

    monkeypatch.setattr(rt.WrapRunner, "_pty_spawn", _capture)

    # HATS-707: the wrapper re-heals git hooks at session start (replacing the
    # old SessionStart lifecycle-hook dispatch). Stub it — no .githooks/ here.
    monkeypatch.setattr(rt.WrapRunner, "_resync_managed_hooks", lambda self, session=None, result=None: [], raising=False)

    # Suppress the "(re-)assembling …" stdout chatter from
    # Assembler.set_role and friends — keeps test output clean.
    monkeypatch.setenv("AI_HATS_QUIET", "1")


# --------------------------------------------------------------------- #
# The regression test
# --------------------------------------------------------------------- #


# Unique substrings emitted by maintainer composition.
# - "E2E gate"        — from trait ai-hats-maintainer
# - "Agent Protocol"  — from trait trait-agent
# - "primary development assistant for the" — from role maintainer's own
#   injection intro (HATS-703 dropped the old "## Workflow" marker section).
TRAIT_MARKER_E2E_GATE = "E2E gate"
TRAIT_MARKER_AGENT_PROTOCOL = "Agent Protocol"
ROLE_MARKER_INTRO = "primary development assistant for the"
SECTION_HEADER_RULES = "## RULES"
SECTION_HEADER_PRIORITIES = "## PRIORITIES"


def test_bare_session_includes_full_role_injection(
    project_with_maintainer_default: Path, monkeypatch
):
    """Bare ``ai-hats`` (no ``--role``) — the canonical HATS-452 trigger.

    ``active_role: maintainer`` in ``ai-hats.yaml`` makes the runtime
    resolve to the maintainer role. The composed system prompt MUST
    contain the trait/role injection bodies. Pre-fix this fails: prompt
    is missing every marker.
    """
    sink: dict[str, Any] = {}
    _install_pty_capture(monkeypatch, sink)

    result = CliRunner().invoke(main, [])

    assert result.exit_code == 0, (
        f"bare ai-hats exited {result.exit_code}\n"
        f"stdout:\n{result.output}\n"
        f"exc:\n{result.exception!r}"
    )
    assert "prompt_text" in sink, (
        f"--system-prompt-file argv not captured. cmd was: {sink.get('cmd')!r}"
    )
    text = sink["prompt_text"]
    assert text, f"prompt.md was empty/missing at {sink.get('prompt_path')!r}"

    # The smoking-gun assertions. Each marker comes from a different
    # composition source (role / two distinct traits) — a regression in
    # any one of {compose_role, funnel, WrapRunner override} drops at
    # least one of these markers.
    assert TRAIT_MARKER_E2E_GATE in text, (
        f"HATS-452 regression: trait ai-hats-maintainer injection missing "
        f"({TRAIT_MARKER_E2E_GATE!r} not in prompt). Full prompt:\n{text}"
    )
    assert TRAIT_MARKER_AGENT_PROTOCOL in text, (
        f"HATS-452 regression: trait trait-agent injection missing "
        f"({TRAIT_MARKER_AGENT_PROTOCOL!r} not in prompt). Full prompt:\n{text}"
    )
    assert ROLE_MARKER_INTRO in text, (
        f"HATS-452 regression: role maintainer's own injection missing "
        f"({ROLE_MARKER_INTRO!r} not in prompt). Full prompt:\n{text}"
    )

    # Layout invariant: injection sits between PRIORITIES and RULES.
    pri_pos = text.find(SECTION_HEADER_PRIORITIES)
    rules_pos = text.find(SECTION_HEADER_RULES)
    e2e_pos = text.find(TRAIT_MARKER_E2E_GATE)
    assert 0 <= pri_pos < e2e_pos < rules_pos, (
        f"injection in wrong layout slot: "
        f"PRIORITIES@{pri_pos}, E2E-gate@{e2e_pos}, RULES@{rules_pos}"
    )


def test_explicit_role_session_includes_full_role_injection(
    project_with_maintainer_default: Path, monkeypatch
):
    """``ai-hats --role maintainer`` (explicit) — must also carry the
    injection. Pre-fix this path also happens to work (role propagates
    correctly through ComposeRole), but lock the contract so a future
    refactor cannot regress only this branch.
    """
    sink: dict[str, Any] = {}
    _install_pty_capture(monkeypatch, sink)

    result = CliRunner().invoke(main, ["--role", "maintainer"])

    assert result.exit_code == 0, (
        f"ai-hats --role maintainer exited {result.exit_code}\n"
        f"stdout:\n{result.output}\nexc:\n{result.exception!r}"
    )
    text = sink.get("prompt_text", "")
    assert TRAIT_MARKER_E2E_GATE in text, (
        f"--role maintainer regression: {TRAIT_MARKER_E2E_GATE!r} missing"
    )
    assert TRAIT_MARKER_AGENT_PROTOCOL in text
    assert ROLE_MARKER_INTRO in text
