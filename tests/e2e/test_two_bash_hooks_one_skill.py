"""e2e (HATS-1917)

flow:   a skill declares TWO PreToolUse/Bash scripts and a session composes it
cmds:
    ai-hats self init -p claude -r maintainer --no-wizard
expect: both scripts reach the composed Bash chain, and both act — one nudges on
        its own marker, the other denies on its own
why:    the loader used to refuse the second row outright, and the refusal took
        the whole CLI down with it; the wiring that made that refusal necessary
        (one managed settings entry per (event, skill, matcher)) has since been
        replaced by a manifest list behind a dispatcher, so nothing was left to
        collapse onto — but nothing proved the second row survives end to end
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.git import init_repo
from _helpers.hook_chain import build_session_settings, pretooluse_hooks, run_chain

pytestmark = [pytest.mark.integration, pytest.mark.guards]

SKILL = "worktree-isolation"
SESSION_ID = "sid-two-hooks"

#: Each probe answers to its own marker, so a verdict names which one acted.
NUDGE_MARKER = "PROBE_NUDGE_ME"
DENY_MARKER = "PROBE_DENY_ME"
NUDGE_TEXT = "PROBE-ONE spoke"
DENY_TEXT = "PROBE-TWO spoke"

_PROBE = '''#!/usr/bin/env python3
"""HATS-1917 e2e probe — answers only to its own marker."""
import json
import sys

payload = json.loads(sys.stdin.read() or "{{}}")
tool_input = payload.get("tool_input") or {{}}
command = tool_input.get("command") or ""
if "{marker}" in command:
    print(json.dumps({{"hookSpecificOutput": {{
        "hookEventName": "PreToolUse",
        {verdict}
    }}}}))
'''

_NUDGE_VERDICT = f'"additionalContext": "{NUDGE_TEXT}",'
_DENY_VERDICT = f'"permissionDecision": "deny", "permissionDecisionReason": "{DENY_TEXT}",'


def _builtin_skill_dir() -> Path:
    """The shipped skill source, asked of the engine rather than spelled out."""
    from ai_hats.paths import builtin_library_root

    root = builtin_library_root()
    assert root is not None, "no builtin library root — the install under test is broken"
    return root / "core" / "skills" / SKILL


def _skill_with_two_bash_probes(project: Path) -> Path:
    """Override the skill in the project's own library layer, declaring two
    Bash scripts where the shipped one declares a single hook per matcher.

    A whole-directory copy is what an override IS here: components resolve
    last-wins per directory."""
    dest = project / "libraries" / "skills" / SKILL
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _builtin_skill_dir(),
        dest,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name, marker, verdict in (
        ("probe_one.py", NUDGE_MARKER, _NUDGE_VERDICT),
        ("probe_two.py", DENY_MARKER, _DENY_VERDICT),
    ):
        script = dest / "hooks" / name
        script.write_text(_PROBE.format(marker=marker, verdict=verdict))
        script.chmod(0o755)
    (dest / "SKILL.md").write_text(
        "---\n"
        f"name: {SKILL}\n"
        "ai_hats:\n"
        "  runtime_hooks:\n"
        "    PreToolUse:\n"
        "      - matcher: Bash\n"
        "        script: hooks/probe_one.py\n"
        "      - matcher: Bash\n"
        "        script: hooks/probe_two.py\n"
        "---\n\n"
        "# Worktree Isolation\n\nTwo Bash guards, on purpose.\n"
    )
    return dest


@pytest.fixture(scope="module")
def project(shared_launcher, tmp_path_factory):
    """A real project whose role composes the overridden skill."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("two-hooks-home"))

    proj = tmp_path_factory.mktemp("two-hooks-proj")
    init_repo(proj)
    _skill_with_two_bash_probes(proj)
    result = subprocess.run(  # noqa: S603 - launcher path from the session fixture
        [str(launcher), "self", "init", "-p", "claude", "-r", "maintainer", "--no-wizard"],
        cwd=str(proj),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise AssertionError(f"self init failed:\n{result.stdout}\n{result.stderr}")
    settings = build_session_settings(proj, role="maintainer", session_id=SESSION_ID)
    return proj, env, settings


def test_both_scripts_reach_the_composed_bash_chain(project):
    """The positive control for both firing tests: a hook that never loaded is
    silent in exactly the way a hook that judged and approved is."""
    proj, _env, settings = project
    wired = pretooluse_hooks(settings, "Bash")
    assert any("probe_one.py" in c for c in wired), f"first probe missing: {wired}"
    assert any("probe_two.py" in c for c in wired), f"second probe missing: {wired}"


def test_the_first_hook_still_speaks(project):
    proj, env, settings = project
    verdict = run_chain(proj, f"echo {NUDGE_MARKER}", settings=settings, env=env, cwd=proj)
    assert NUDGE_TEXT in verdict.context, verdict
    assert not verdict.gated, f"a nudge must not gate: {verdict}"


def test_the_second_hook_speaks_too(project):
    """The row that could not exist before this card."""
    proj, env, settings = project
    verdict = run_chain(proj, f"echo {DENY_MARKER}", settings=settings, env=env, cwd=proj)
    assert verdict.denied, f"expected a deny, got {verdict}"
    assert DENY_TEXT in verdict.reason, verdict


def test_a_command_neither_probe_claims_passes_clean(project):
    """Neither hook is a blanket verdict — otherwise the two above prove nothing
    about which one acted."""
    proj, env, settings = project
    verdict = run_chain(proj, "echo nothing to see", settings=settings, env=env, cwd=proj)
    assert not verdict.gated, verdict
    assert NUDGE_TEXT not in verdict.context, verdict
    assert DENY_TEXT not in verdict.context, verdict


def test_the_manifest_gives_the_two_rows_distinct_tags(project):
    """What the dispatcher reads. Same-tag rows would report a firing without
    saying which gate fired."""
    from ai_hats.surfaces.claude.profile import PROFILE

    _proj, _env, settings = project
    # The session's cache dir, taken from the settings file the session actually
    # wrote: the fixture gives the subprocess its own HOME, so re-deriving the
    # root in this process lands in a different cache home entirely.
    manifest = PROFILE.manifest_path(settings.parent)
    assert manifest.is_file(), f"no session hook manifest at {manifest}"
    rows = json.loads(manifest.read_text())["hooks"]["PreToolUse"]
    probes = [r for r in rows if "probe_" in r["command"]]
    assert len(probes) == 2, f"the manifest lost a row: {rows}"
    assert len({r["tag"] for r in probes}) == 2, f"two rows share one tag: {probes}"
