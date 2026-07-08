"""E2E regression: HATS-523 — HITL session must persist the materialized
system prompt to ``<session_dir>/meta_prompt.txt``.

Triggering gap. Before HATS-523, the HITL path (``WrapRunner.run``) wrote
the composed prompt to ``<cache>/sessions/<sid>/prompt.md`` and passed it
via ``--system-prompt-file``, but that cache file was deleted by
``_cleanup_session_cache`` in the ``finally`` block. After session end,
``session_dir`` contained ``audit.md``, ``metrics.json``, ``transcript.txt``,
``reasoning.log``, ``trace.log`` — but NO record of what the provider
actually saw. ``SubAgentRunner`` (Automate path) already persisted this via
``session.save_meta_prompt(...)``; HITL was asymmetric.

Scope of this test. Drives bare ``ai-hats`` (no ``--role``,
``active_role=maintainer``) through the full ``human`` pipeline with this
repo's real ``library/``. ``WrapRunner._pty_spawn`` is stubbed so we don't
launch a real Claude binary, but every step BEFORE the spawn — composition,
``provider.build_session_prompt`` (which now returns the materialized text
as the 3rd tuple element), ``session.save_meta_prompt`` — runs for real.
We then assert ``<session_dir>/meta_prompt.txt`` exists with the expected
role/trait markers.

Fail-under-revert (per ``dev_rule_e2e_gate`` §4). Reverting any of:

- ``providers.py`` (drop ``meta_prompt`` from ``build_session_prompt``
  3-tuple) → unpack error in ``WrapRunner.run``, test fails on session
  exit code.
- ``runtime.py`` (drop ``session.save_meta_prompt(meta_prompt)`` from
  ``WrapRunner.run``) → ``meta_prompt.txt`` is missing, assertion fails.

Companion to ``test_session_prompt_contains_role_injection.py`` — that test
inspects the in-cache ``prompt.md`` mid-session; THIS test inspects the
persistent ``meta_prompt.txt`` post-session, which is the artefact e2e
tools / audit / retro consumers actually have access to.

Deliberate long e2e regression scenario contract — noqa: comment-length.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.models import ProjectConfig
from ai_hats_observe.artifacts import META_PROMPT_TXT
from ai_hats.paths import PROJECT_CONFIG


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "library"


pytestmark = pytest.mark.integration


# Unique substrings produced by composing the maintainer role with its
# default trait set. Each comes from a different composition source —
# regression in role injection / trait injection / merge would drop one.
TRAIT_MARKER_E2E_GATE = "E2E gate"          # trait ai-hats-maintainer
TRAIT_MARKER_AGENT_PROTOCOL = "Agent Protocol"  # trait trait-agent
# role maintainer's own injection intro (HATS-703 dropped the "## Workflow" marker)
ROLE_MARKER_INTRO = "primary development assistant for the"


@pytest.fixture
def project_with_maintainer_default(tmp_path: Path, monkeypatch) -> Path:
    """Tmp project mirroring production layout: real ``library/`` wired in,
    ``active_role=maintainer`` so bare ``ai-hats`` resolves to maintainer.
    """
    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[LIBRARY_DIR])
    asm.init()
    asm.set_role("maintainer", provider_name="claude")
    monkeypatch.chdir(project)
    return project


def _install_pty_capture(monkeypatch, sink: dict[str, Any]) -> None:
    """Stub ``_pty_spawn`` so the test doesn't depend on a real Claude
    binary, but the wrapper's ``finally`` (cleanup + finalize) still runs.
    Mirrors the helper from ``test_session_prompt_contains_role_injection``.
    """
    from ai_hats import runtime as rt

    def _capture(_self, cmd, env, tracer):  # noqa: ARG001
        sink["cmd"] = list(cmd)
        return 0

    monkeypatch.setattr(rt.WrapRunner, "_pty_spawn", _capture)

    # HATS-707 → HATS-833: session start re-heals managed hooks; stub it
    # (no hook surfaces under test here). Returns no startup notices.
    monkeypatch.setattr(
        rt.WrapRunner,
        "_resync_managed_hooks",
        lambda self, session=None, result=None: [],
        raising=False,
    )
    monkeypatch.setenv("AI_HATS_QUIET", "1")


def _find_latest_session_dir(project: Path) -> Path:
    """Return the single ``session_<sid>/`` dir under
    ``<project>/.agent/ai-hats/sessions/runs/`` — the bare-HITL run created
    exactly one. Fail with a diagnostic if zero or more than one exist.
    """
    runs = project / ".agent" / "ai-hats" / "sessions" / "runs"
    if not runs.exists():
        raise AssertionError(f"no sessions/runs dir under {runs}")
    sessions = sorted(runs.glob("session_*"))
    if len(sessions) != 1:
        raise AssertionError(
            f"expected exactly 1 session dir, got {len(sessions)}: "
            f"{[s.name for s in sessions]}"
        )
    return sessions[0]


def test_hitl_session_persists_meta_prompt_to_session_dir(
    project_with_maintainer_default: Path, monkeypatch
):
    """After a bare-``ai-hats`` HITL session, ``<session_dir>/meta_prompt.txt``
    exists, is non-empty, and contains the same role/trait injection bodies
    that reached the provider via ``--system-prompt-file``.
    """
    sink: dict[str, Any] = {}
    _install_pty_capture(monkeypatch, sink)

    result = CliRunner().invoke(main, [])

    assert result.exit_code == 0, (
        f"bare ai-hats exited {result.exit_code}\n"
        f"stdout:\n{result.output}\n"
        f"exc:\n{result.exception!r}"
    )

    session_dir = _find_latest_session_dir(project_with_maintainer_default)
    meta_prompt_path = session_dir / META_PROMPT_TXT

    assert meta_prompt_path.exists(), (
        f"HATS-523 regression: <session_dir>/meta_prompt.txt missing.\n"
        f"session_dir contents: {[p.name for p in session_dir.iterdir()]}"
    )

    text = meta_prompt_path.read_text()
    assert text.strip(), (
        f"HATS-523 regression: meta_prompt.txt exists but is empty/blank.\n"
        f"path: {meta_prompt_path}"
    )

    # Same trio of unique markers used by HATS-452's mid-session capture —
    # the meta_prompt.txt MUST carry the merged composition that
    # ``--system-prompt-file`` carried. If any marker is missing, either
    # (a) the 3-tuple's meta_prompt element is wrong (provider regression),
    # or (b) WrapRunner is saving the wrong text (e.g. raw build_system_prompt
    # without HATS-380 expansion or without the INJECTION markers).
    for marker in (
        TRAIT_MARKER_E2E_GATE,
        TRAIT_MARKER_AGENT_PROTOCOL,
        ROLE_MARKER_INTRO,
    ):
        assert marker in text, (
            f"HATS-523 regression: meta_prompt.txt missing marker "
            f"{marker!r}. Full content:\n{text}"
        )


def test_hitl_meta_prompt_matches_system_prompt_file_bytes(
    project_with_maintainer_default: Path, monkeypatch
):
    """``meta_prompt.txt`` (persistent) MUST be byte-identical to the
    contents of the ``--system-prompt-file`` argv (the cache file passed to
    Claude). Pins the contract that the persistent artefact is EXACTLY what
    the provider saw — not a summary, not a separate render.
    """
    sink: dict[str, Any] = {}

    # Capture the --system-prompt-file content BEFORE cache cleanup.
    from ai_hats import runtime as rt

    def _capture(_self, cmd, env, tracer):  # noqa: ARG001
        sink["cmd"] = list(cmd)
        for i, tok in enumerate(cmd):
            if tok == "--system-prompt-file" and i + 1 < len(cmd):
                p = Path(cmd[i + 1])
                sink["prompt_text"] = p.read_text() if p.exists() else None
                break
        return 0

    monkeypatch.setattr(rt.WrapRunner, "_pty_spawn", _capture)

    # HATS-707 → HATS-833: session start re-heals managed hooks; stub it
    # (no hook surfaces under test here). Returns no startup notices.
    monkeypatch.setattr(
        rt.WrapRunner,
        "_resync_managed_hooks",
        lambda self, session=None, result=None: [],
        raising=False,
    )
    monkeypatch.setenv("AI_HATS_QUIET", "1")

    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, (
        f"exit={result.exit_code} exc={result.exception!r}"
    )

    cache_text = sink.get("prompt_text")
    assert cache_text, "did not capture --system-prompt-file contents"

    session_dir = _find_latest_session_dir(project_with_maintainer_default)
    persisted_text = (session_dir / META_PROMPT_TXT).read_text()

    assert persisted_text == cache_text, (
        "HATS-523 contract: meta_prompt.txt must be byte-identical to the "
        "file passed via --system-prompt-file. "
        f"persisted len={len(persisted_text)}, cache len={len(cache_text)}"
    )
