"""e2e (HATS-1170, HATS-1203)

flow:   a developer displaying session prompt configuration when custom user rules are
        defined in user-rules/
cmds:
    ai-hats config show-prompt
expect: output includes all markdown rules from user-rules/ formatted under
        the USER RULES section heading
why:    user-defined rules in user-rules/ must be incorporated into system
        prompts across all composition interfaces
"""
# comment-length: allow — fail-under-revert contract, dev_rule_e2e_gate §4

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.placeholders import expand_path_placeholders


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"

INJECTION_START = "<!-- AI-HATS:START -->"
INJECTION_END = "<!-- AI-HATS:END -->"

SECTION_USER_RULES = "## USER RULES"
SECTION_RULES = "## RULES"

# Deliberately unmistakable: a substring that cannot occur anywhere in the
# framework's own library, so a hit proves it came from the user's file.
SENTINEL = "HATS-1203-USER-RULE-SENTINEL-Zq7"
RULE_STEM = "my-team-rule"
RULE_BODY = f"# Team rule\n\nAlways cite the ledger id: {SENTINEL}\n"


# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke]


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #


def _write_project(project: Path) -> None:
    """Config + bootstrap + maintainer role — the production layout."""
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


def _add_user_rule(project: Path, stem: str = RULE_STEM, body: str = RULE_BODY) -> Path:
    """Drop a rule into the DATA-layer landing zone, as a consumer would."""
    user_rules = project / ".agent" / "ai-hats" / "user-rules"
    user_rules.mkdir(parents=True, exist_ok=True)
    target = user_rules / f"{stem}.md"
    target.write_text(body)
    return target


@pytest.fixture
def project_with_user_rule(tmp_path: Path, monkeypatch) -> Path:
    """In-process project (CliRunner tier) carrying one user-rule."""
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project)
    _add_user_rule(project)
    monkeypatch.chdir(project)
    # bootstrap_or_die does a self-update probe — stub for offline / CI.
    import ai_hats._bootstrap as boot

    monkeypatch.setattr(boot, "bootstrap_or_die", lambda: None)
    return project


@pytest.fixture
def subprocess_project_with_user_rule(tmp_path: Path, ai_hats_shim: Path):
    """Real-binary tier: a :class:`Project` driver over the same layout.

    ``dev_rule_e2e_gate`` does not accept in-process ``CliRunner`` coverage for
    a change that touches ``src/ai_hats/cli/`` — this fixture is what makes the
    gate-satisfying test a real ``ai-hats`` subprocess.
    """
    from _helpers.project import Project

    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project)
    _add_user_rule(project)
    return Project(path=project, ai_hats_binary=ai_hats_shim)


# --------------------------------------------------------------------- #
# Capture helper (mirrors the HATS-452 / HATS-456 e2e shape)
# --------------------------------------------------------------------- #


def _install_pty_capture(monkeypatch, sink: dict[str, Any]) -> None:
    from ai_hats import runtime as rt

    def _capture(_self, cmd, env, tracer, pty_tap_factory=None, on_spawn=None):  # noqa: ARG001
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
    monkeypatch.setattr(
        rt.WrapRunner,
        "_resync_managed_hooks",
        lambda self, session=None, result=None: [],
        raising=False,
    )
    monkeypatch.setenv("AI_HATS_QUIET", "1")


def _extract_session_block_body(text: str) -> str:
    """Body between the AI-HATS markers, markers excluded."""
    start_idx = text.find(INJECTION_START)
    end_idx = text.find(INJECTION_END)
    assert start_idx >= 0, f"INJECTION_START missing:\n{text[:500]}..."
    assert end_idx > start_idx, (
        f"INJECTION_END missing or before START (start={start_idx}, end={end_idx})"
    )
    return text[start_idx + len(INJECTION_START) : end_idx].strip("\n")


# --------------------------------------------------------------------- #
# The regression tests
# --------------------------------------------------------------------- #


def test_user_rule_reaches_show_prompt_real_binary(subprocess_project_with_user_rule):
    """``ai-hats config show-prompt`` — real binary, real subprocess.

    The gate-satisfying tier (``dev_rule_e2e_gate``): this is the surface a
    user checks to answer "what does my agent actually see?". Pre-fix it
    answers without their rules.
    """
    from _helpers.env import checkout_pythonpath

    res = subprocess_project_with_user_rule.run(
        "config",
        "show-prompt",
        # The autouse scrub strips PYTHONPATH so install-tier tests exercise the
        # installed artefact; this tier must run THIS checkout (worktree-correct).
        extra_env={"PYTHONPATH": checkout_pythonpath(REPO_ROOT)},
    )
    res.expect_ok()

    assert SENTINEL in res.stdout, (
        f"HATS-1203 regression: user-rule body absent from show-prompt.\n"
        f"  expected sentinel: {SENTINEL!r}\n"
        f"  stdout (tail 800):\n{res.stdout[-800:]}"
    )
    assert SECTION_USER_RULES in res.stdout, (
        f"{SECTION_USER_RULES!r} header missing from show-prompt output"
    )
    assert f"### {RULE_STEM}" in res.stdout, (
        f"per-file heading '### {RULE_STEM}' missing from show-prompt output"
    )


def test_user_rule_reaches_session_prompt(project_with_user_rule: Path, monkeypatch):
    """The file actually handed to ``claude`` via ``--system-prompt-file``.

    This is the literal HATS-1203 repro: a rule sits in ``user-rules/``, the
    session starts, and pre-fix the prompt does not contain it.
    """
    sink: dict[str, Any] = {}
    _install_pty_capture(monkeypatch, sink)

    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, (
        f"bare ai-hats exited {result.exit_code}\n"
        f"stdout:\n{result.output}\nexc:\n{result.exception!r}"
    )

    text = sink.get("prompt_text")
    assert text, (
        f"session prompt missing/empty (path={sink.get('prompt_path')!r}, cmd={sink.get('cmd')!r})"
    )
    assert SENTINEL in text, (
        f"HATS-1203 regression: user-rule body never reached the session prompt.\n"
        f"  expected sentinel: {SENTINEL!r}\n"
        f"  prompt (tail 800):\n{text[-800:]}"
    )

    # Layout invariant (R6): USER RULES sits after the framework RULES block.
    rules_pos = text.find(SECTION_RULES)
    user_pos = text.find(SECTION_USER_RULES)
    assert 0 <= rules_pos < user_pos, (
        f"section order wrong: {SECTION_RULES}@{rules_pos}, "
        f"{SECTION_USER_RULES}@{user_pos} (expected RULES first)"
    )


def test_show_prompt_and_session_agree_on_user_rules(project_with_user_rule: Path, monkeypatch):
    """Preview and session must be derived from the same composition.

    ``test_show_prompt_matches_session_prompt.py`` already pins byte-equality,
    but its fixture has no user-rules — so the equality holds trivially over
    the new section. This re-runs the contract WITH a rule present, which is
    what makes it a real guard for the HATS-1203 design (attach at the single
    ``compose_for_role`` funnel, not per-consumer).
    """
    sink: dict[str, Any] = {}
    _install_pty_capture(monkeypatch, sink)

    res_session = CliRunner().invoke(main, [])
    assert res_session.exit_code == 0, (
        f"bare ai-hats exited {res_session.exit_code}\n{res_session.output}"
    )
    session_body = _extract_session_block_body(sink.get("prompt_text") or "")

    res_show = CliRunner().invoke(main, ["config", "show-prompt"])
    assert res_show.exit_code == 0, f"show-prompt exited {res_show.exit_code}"
    show_body = expand_path_placeholders(
        res_show.output, ProjectLayout.at(project_with_user_rule)
    ).strip("\n")

    assert SENTINEL in session_body, "sentinel missing from session side"
    assert SENTINEL in show_body, "sentinel missing from show-prompt side"
    assert show_body == session_body, (
        "show-prompt and session-prompt diverged with a user-rule present.\n"
        f"  show len:    {len(show_body)}\n"
        f"  session len: {len(session_body)}\n"
    )


def test_no_user_rules_section_when_directory_empty(tmp_path: Path, monkeypatch):
    """No rules → no header. Guards against emitting a bare ``## USER RULES``
    (or a stray blank separator) on the overwhelmingly common empty case.
    """
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project)
    # init creates user-rules/ — leave it empty, and add a non-.md decoy.
    (project / ".agent" / "ai-hats" / "user-rules").mkdir(parents=True, exist_ok=True)
    (project / ".agent" / "ai-hats" / "user-rules" / "notes.txt").write_text(
        f"not markdown: {SENTINEL}\n"
    )
    monkeypatch.chdir(project)
    import ai_hats._bootstrap as boot

    monkeypatch.setattr(boot, "bootstrap_or_die", lambda: None)

    res = CliRunner().invoke(main, ["config", "show-prompt"])
    assert res.exit_code == 0, f"show-prompt exited {res.exit_code}\n{res.output}"
    assert SECTION_USER_RULES not in res.output, (
        f"emitted a {SECTION_USER_RULES!r} header with no .md rules present"
    )
    assert SENTINEL not in res.output, (
        "non-.md file in user-rules/ was picked up — glob must be *.md only"
    )
