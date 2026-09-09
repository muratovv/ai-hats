"""e2e (HATS-1170, HATS-1336)

flow:   a developer running ai-hats self update to sweep legacy root residue files
cmds:
    ai-hats self update
expect: self update command sweeps legacy root residue files and keeps framework state inside
        .agent/ai-hats/
why: without root residue sweeps, legacy config files remain in project root corrupting
     state resolution"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from ai_hats.constants import HOOK_POST_TOOL_USE, HOOK_PRE_TOOL_USE
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG

pytestmark = pytest.mark.install

# A user-authored entry, byte-identical before and after. Carries no ai-hats
# tag under any key — the sole thing that distinguishes it from the residue.
USER_ENTRY = {
    "matcher": "*",
    "hooks": [{"type": "command", "command": "user-own.sh"}],
}


def _git(project_dir: Path, *args: str, env: dict[str, str]) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(project_dir),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _bump(project: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [f"{env[ENV_AI_HATS_VENV]}/bin/python", "-m", "ai_hats._bump_internal"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"bump failed ({result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _seed(project: Path, env: dict[str, str]) -> None:
    """A project carrying both retired schemes' root residue.

    ``.gemini/settings.json`` gets NO user entry (matching the real pre-1166
    remnant, which is 100% ai-hats) so it exercises the empty-file contract;
    ``.claude/settings.json`` gets one, so it exercises surgical removal.
    """
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    (project / ".agent" / "ai-hats").mkdir(parents=True)

    claude_settings = project / ".claude" / "settings.json"
    claude_settings.parent.mkdir()
    claude_settings.write_text(
        json.dumps(
            {
                "hooks": {
                    HOOK_PRE_TOOL_USE: [
                        {
                            "matcher": "Bash",
                            "_ai_hats_managed": "ai-hats:hats-437",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "$CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/"
                                        "pre_bash_shared_state_guard.sh"
                                    ),
                                }
                            ],
                        },
                        USER_ENTRY,
                    ],
                    # Both managed entries name a package-data-owned hook that
                    # bump actually materializes. Two guards otherwise fire
                    # first and mask the sweep: the HATS-549 healer drops an
                    # entry whose basename is outside the owned whitelist, and
                    # bump REFUSES outright on a command that resolves to no
                    # file. Neither is what this test is about.
                    HOOK_POST_TOOL_USE: [
                        {
                            "matcher": "Edit|Write|MultiEdit",
                            "_ai_hats_managed": "ai-hats:py-security-lint:PostToolUse:Edit",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "$CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/"
                                        "pre_bash_shared_state_guard.sh"
                                    ),
                                }
                            ],
                        }
                    ],
                }
            },
            indent=2,
        )
        + "\n"
    )

    # Pre-HATS-1166 agy remnant: the tag lives under ``tag``, NOT
    # ``_ai_hats_managed`` — same ``ai-hats:`` prefix, different key.
    gemini_settings = project / ".gemini" / "settings.json"
    gemini_settings.parent.mkdir()
    gemini_settings.write_text(
        json.dumps(
            {
                "hooks": {
                    HOOK_PRE_TOOL_USE: [
                        {
                            "matcher": "Bash",
                            "tag": "ai-hats:tool-call-hygiene:PreToolUse:Bash",
                            "command": (f"{project}/.agy/skills/tool-call-hygiene/hooks/guard.sh"),
                        }
                    ],
                    HOOK_POST_TOOL_USE: [
                        {
                            "matcher": "Edit|Write",
                            "tag": "ai-hats:markdown-format:PostToolUse:Edit|Write",
                            "command": (
                                f"{project}/.agent/ai-hats/.cache/sessions/"
                                "20260724-172755-1/rules/.agents/skills/"
                                "markdown-format/hooks/post_md_format.py"
                            ),
                        }
                    ],
                }
            },
            indent=2,
        )
        + "\n"
    )

    _git(project, "init", "-q", "-b", "main", env=env)
    _git(project, "config", "user.email", "test@example.com", env=env)
    _git(project, "config", "user.name", "Test", env=env)
    _git(project, "add", "-A", env=env)
    _git(project, "commit", "-q", "-m", "seed", env=env)


def _tags(settings: Path, key: str) -> list[str]:
    """Every ``ai-hats:``-prefixed tag left in ``settings``, under ``key``."""
    data = json.loads(settings.read_text())
    found = []
    for entries in data.get("hooks", {}).values():
        for entry in entries:
            tag = str(entry.get(key, ""))
            if tag.startswith("ai-hats:"):
                found.append(tag)
    return found


@pytest.fixture
def seeded(shared_launcher, tmp_path):
    _launcher, env, _venv = shared_launcher
    project = tmp_path / "proj"
    project.mkdir()
    _seed(project, env)
    return project, env


@pytest.mark.integration
def test_bump_sweeps_gemini_root_residue(seeded):
    """The pre-1166 ``.gemini/settings.json`` remnant is reclaimed.

    Its tag key is ``tag``; recognising it is what makes the sweep surgical
    rather than a path-substring heuristic.
    """
    project, env = seeded
    gemini = project / ".gemini" / "settings.json"

    _bump(project, env)

    assert _tags(gemini, "tag") == [], (
        f"pre-1166 residue survived the sweep: {_tags(gemini, 'tag')}"
    )


@pytest.mark.integration
def test_bump_sweeps_claude_root_residue(seeded):
    """The pre-1170 ``.claude/settings.json`` managed entries are reclaimed.

    Nothing else can: ``ensure_runtime_hooks`` is a no-op, so while the
    ``runtime-hooks`` owner stayed registered these entries were unreachable
    by any code path (HATS-905 designed dropping that registration as the
    retirement switch).
    """
    project, env = seeded
    claude = project / ".claude" / "settings.json"

    _bump(project, env)

    assert _tags(claude, "_ai_hats_managed") == [], (
        f"pre-1170 residue survived the sweep: {_tags(claude, '_ai_hats_managed')}"
    )


@pytest.mark.integration
def test_user_entry_survives_sweep_byte_identical(seeded):
    """R3: the sweep is surgical — a user entry beside the residue is untouched.

    Asserted on the entry's own serialization, not on its mere presence: this
    repo's real root files carry ZERO user entries, so a presence-only check
    would pass while proving nothing.
    """
    project, env = seeded
    claude = project / ".claude" / "settings.json"
    before = json.dumps(USER_ENTRY, sort_keys=True)

    _bump(project, env)

    survivors = [
        json.dumps(entry, sort_keys=True)
        for entries in json.loads(claude.read_text()).get("hooks", {}).values()
        for entry in entries
    ]
    assert survivors == [before], f"user entry not preserved byte-identically: {survivors}"


@pytest.mark.integration
def test_root_carries_no_ai_hats_hook_commands_after_sweep(seeded):
    """The property that survives HATS-1268: a clean root cannot double-fire.

    Measured 2026-08-01 — claude merges root with ``--settings`` additively and
    collapses byte-identical commands, which is the only reason today's
    duplicated wiring fires once; HATS-1268 diverges the two spellings and ends
    that. Asserting on the root, not on claude's dedup, keeps this independent
    of third-party behaviour.
    """
    project, env = seeded

    _bump(project, env)

    for settings in (project / ".claude" / "settings.json", project / ".gemini" / "settings.json"):
        commands = [
            str(hook.get("command", ""))
            for entries in json.loads(settings.read_text()).get("hooks", {}).values()
            for entry in entries
            for hook in (entry.get("hooks") or [entry])
        ]
        assert not [c for c in commands if ".agent/ai-hats/" in c or "/.agy/" in c], (
            f"{settings.name} still carries ai-hats wiring: {commands}"
        )


@pytest.mark.integration
def test_named_remedy_command_reaches_the_gemini_surface(seeded, shared_launcher):
    """HATS-1522: the startup WARN names ``self init --no-wizard`` — this proves
    that command reaches the agy surface the startup scan reads.

    The other tests here drive ``_bump_internal``; a promise made to a user is
    only kept by the command the user is actually told to type.
    """
    project, env = seeded
    launcher, _env, _venv = shared_launcher
    gemini = project / ".gemini" / "settings.json"
    assert _tags(gemini, "tag"), "seed must start dirty or the test proves nothing"

    result = subprocess.run(
        [str(launcher), "self", "init", "--no-wizard"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert _tags(gemini, "tag") == [], (
        f"residue survived the command the WARN names: {_tags(gemini, 'tag')}"
    )


@pytest.mark.integration
def test_swept_gemini_settings_stays_as_empty_object(seeded):
    """R4: an emptied file stays on disk as ``{}``; the dir is not removed.

    Deleting an untracked, gitignored file has no undo path
    (``global_rule_destructive_actions``), so the sweep empties, never unlinks.
    """
    project, env = seeded
    gemini = project / ".gemini" / "settings.json"

    _bump(project, env)

    assert gemini.is_file(), "sweep must not unlink the file"
    assert json.loads(gemini.read_text()) == {}
    assert gemini.parent.is_dir(), "sweep must not remove the parent dir"
