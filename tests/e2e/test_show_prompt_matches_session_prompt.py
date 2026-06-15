"""E2E contract: ``ai-hats config show-prompt`` agrees with the real session
prompt — they MUST be derived from the same composition (HATS-456 Phase 0).

Background. HATS-452 Phase 1 introduced ``MaterializeSystemPrompt`` +
``ai-hats config show-prompt`` as the canonical "what does the agent see
for role X" surface. But four sites still inline ``composer.compose +
provider.build_system_prompt``:

  1. ``WrapRunner.run_session``      — HITL (this test exercises this path)
  2. ``SubAgentRunner._run_attempt`` — Automate (out-of-scope here)
  3. ``Assembler.set_role.build``    — on-disk write (out-of-scope here)
  4. ``MaterializeSystemPrompt.run`` — preview (this test exercises this path)

If sites 1 and 4 diverge, ``show-prompt`` lies about what the agent
will see. This test pins them together: the AI-HATS injection block
inside the materialized text MUST be byte-equal between the two paths
(after placeholder expansion, which only the session-prompt path
performs today).

Pass/fail policy. The test should pass on master today (no observable
bug — sites are accidentally aligned). HATS-456 Phase 1 turns
"accidentally" into "structurally impossible to diverge" by routing
both sites through a single ``compose_for_role`` facade. Failure of
this test in the future signals drift the facade is supposed to make
impossible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.models import ProjectConfig
from ai_hats.placeholders import expand_path_placeholders


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "library"

INJECTION_START = "<!-- AI-HATS:START -->"
INJECTION_END = "<!-- AI-HATS:END -->"


pytestmark = pytest.mark.integration


# --------------------------------------------------------------------- #
# Fixture (mirrors test_session_prompt_contains_role_injection.py)
# --------------------------------------------------------------------- #


@pytest.fixture
def project_with_maintainer(tmp_path: Path, monkeypatch) -> Path:
    """Tmp project with this repo's real library + active_role=maintainer."""
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
    # bootstrap_or_die does a self-update probe — stub for offline / CI.
    import ai_hats._bootstrap as boot
    monkeypatch.setattr(boot, "bootstrap_or_die", lambda: None)
    return project


# --------------------------------------------------------------------- #
# Capture helper (copy of HATS-452 e2e shape — keep tests independent)
# --------------------------------------------------------------------- #


def _install_pty_capture(monkeypatch, sink: dict[str, Any]) -> None:
    from ai_hats import runtime as rt

    def _capture(_self, cmd, env, tracer):  # noqa: ARG001
        sink["cmd"] = list(cmd)
        for i, tok in enumerate(cmd):
            if tok == "--system-prompt-file" and i + 1 < len(cmd):
                p = Path(cmd[i + 1])
                if p.exists():
                    sink["prompt_text"] = p.read_text()
                sink["prompt_path"] = str(p)
                break
        return 0

    monkeypatch.setattr(rt.WrapRunner, "_pty_spawn", _capture)

    # HATS-707: session start re-heals git hooks; stub it (no .githooks/ here).
    monkeypatch.setattr(rt.WrapRunner, "_resync_git_hooks", lambda self, session=None: None, raising=False)
    monkeypatch.setenv("AI_HATS_QUIET", "1")


def _extract_session_block_body(text: str) -> str:
    """Extract the body BETWEEN ``<!-- AI-HATS:START -->`` and
    ``<!-- AI-HATS:END -->`` markers (markers excluded, surrounding
    whitespace stripped) from a session prompt file.

    Mirrors how ``ClaudeProvider.build_session_prompt`` writes the file:
    it inserts ``f"{START}\\n{prompt_content}\\n{END}"`` where
    ``prompt_content`` is ``build_system_prompt(result)`` after
    placeholder expansion. So stripping the markers + edges yields
    exactly the materialized text.
    """
    start_idx = text.find(INJECTION_START)
    end_idx = text.find(INJECTION_END)
    assert start_idx >= 0, (
        f"INJECTION_START marker missing from session file:\n{text[:500]}..."
    )
    assert end_idx > start_idx, (
        f"INJECTION_END missing or before START in session file "
        f"(start={start_idx}, end={end_idx})"
    )
    body = text[start_idx + len(INJECTION_START) : end_idx]
    return body.strip("\n")


# --------------------------------------------------------------------- #
# The contract test
# --------------------------------------------------------------------- #


def test_show_prompt_block_matches_session_prompt_block(
    project_with_maintainer: Path, monkeypatch
):
    """The AI-HATS injection block in ``ai-hats config show-prompt`` output
    MUST be byte-equal to the AI-HATS block in the file ``ai-hats``
    (bare) hands to the agent via ``--system-prompt-file``, after
    placeholder expansion is applied to the show-prompt side (the
    session-prompt path runs ``expand_path_placeholders`` automatically;
    show-prompt does not).

    HATS-456: this is the contract that Phase 1's ``compose_for_role``
    facade makes structural. Pre-facade it holds by coincidence
    (both sites do ``composer.compose(role, overlays) →
    provider.build_system_prompt(result)`` inline); post-facade it holds
    by construction.
    """
    # ----- Side A: real session prompt via bare `ai-hats` -----
    sink: dict[str, Any] = {}
    _install_pty_capture(monkeypatch, sink)

    res_session = CliRunner().invoke(main, [])
    assert res_session.exit_code == 0, (
        f"bare ai-hats exited {res_session.exit_code}\n"
        f"stdout:\n{res_session.output}\nexc:\n{res_session.exception!r}"
    )
    session_prompt_text = sink.get("prompt_text")
    assert session_prompt_text, (
        f"session --system-prompt-file content missing/empty "
        f"(path={sink.get('prompt_path')!r}, cmd={sink.get('cmd')!r})"
    )
    session_body = _extract_session_block_body(session_prompt_text)

    # ----- Side B: `ai-hats config show-prompt` output -----
    res_show = CliRunner().invoke(main, ["config", "show-prompt"])
    assert res_show.exit_code == 0, (
        f"show-prompt exited {res_show.exit_code}\noutput:\n{res_show.output}"
    )
    show_prompt_text = res_show.output

    # The session-prompt path expands ``{{...}}`` placeholders before
    # writing the file; show-prompt does not (today). Apply the same
    # expansion to the show-prompt side so the comparison is apples-to-
    # apples. If a follow-up moves expansion behind the facade, this
    # call becomes a no-op and the test still holds.
    # ``click.testing.CliRunner`` appends a trailing newline that does
    # not appear inside the session file's marker block — strip both
    # sides before comparing.
    show_body = expand_path_placeholders(
        show_prompt_text, project_with_maintainer
    ).strip("\n")

    # The smoking-gun assertion: the injection block — composed of role
    # injection + trait injections + rule bodies + skill list — must be
    # bit-identical between the two materialization paths.
    assert show_body == session_body, (
        "HATS-456 drift: show-prompt and session-prompt produced different "
        "materialized prompts from the same role.\n"
        f"  show-prompt len:    {len(show_body)}\n"
        f"  session-prompt len: {len(session_body)}\n"
        f"  show-prompt head:\n{show_body[:400]}\n"
        f"  session-prompt head:\n{session_body[:400]}\n"
    )
