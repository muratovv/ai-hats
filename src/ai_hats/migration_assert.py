"""End-of-bump smoke-assert: every hook command path resolves (HATS-549 Phase 3).

Final safety net for the proxmox stuck-state class — ``.claude/settings.json``
referencing hook scripts that don't exist on disk, which Claude Code surfaces as
``/bin/sh: <path>: No such file or directory`` on every Bash call. At the end of
every install-time path (bump / non-greenfield init) we walk every hook command
in the provider settings and assert each path resolves; on failure raise
``assembler.AssemblyError`` with a recovery hint pointing at the Phase 1 backup.

Catches the whole "settings points at nowhere" class regardless of cause
(content-deleting bug, failed migration, manual typo, bad healer rewrite). Scope:
``.claude/settings.json`` + ``settings.local.json``, all hook event types; only
path-like string ``command`` values are checked (bare shell like ``echo foo`` is
skipped). See ``tracker/backlog/tasks/HATS-549/plan.md`` for full design.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .assembler import AssemblyError
from .paths import CLAUDE_PROJECT_DIR_VAR, strip_claude_project_dir

__all__ = [
    "assert_runtime_hooks_resolve",
    "BrokenHookRef",
    "SETTINGS_TARGETS",
]


# Settings files this asserter walks. Matches the Stage A1 healer
# allowlist so the asserter sees the same write surface the healer
# could have touched.
SETTINGS_TARGETS: tuple[str, ...] = (
    ".claude/settings.json",
    ".claude/settings.local.json",
)

# Hook event types the asserter walks. Claude Code's settings.json
# schema groups hooks under these keys; each value is a list of
# ``{matcher, hooks: [{type, command, ...}]}`` blocks. The asserter
# treats the list as opaque — anything with a ``command`` string
# under any hook event is in scope.
_HOOK_EVENT_KEYS: frozenset[str] = frozenset({
    "PreToolUse",
    "PostToolUse",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "Notification",
    "PreCompact",
})

# Variable prefix Claude Code expands at hook-execution time. Stripped
# during the on-disk existence check. Canonical definition lives in
# ``paths`` (HATS-549 Q.1); local alias kept for self-documentation.
_CLAUDE_PROJECT_DIR_VAR = CLAUDE_PROJECT_DIR_VAR


@dataclass(frozen=True)
class BrokenHookRef:
    """One hook entry whose command path doesn't resolve on disk.

    Attributes:
        settings_file: Project-relative path to the settings.json that
            declared the broken hook.
        event: Hook event name (``PreToolUse`` / ``SessionEnd`` / ...).
        command: The literal command string that failed to resolve.
        resolved_path: The absolute path the asserter tried to stat.
    """

    settings_file: str
    event: str
    command: str
    resolved_path: Path


def _looks_like_path(command: str) -> bool:
    """Heuristic: True when ``command`` looks like a file ref we can stat.

    Treats anything containing ``/`` as a path. ``$CLAUDE_PROJECT_DIR``
    placeholder is included by the ``/`` test (the var contains one).

    Avoids false positives on pure shell commands (``echo hello``,
    ``true``, ``exit 0``) — these don't have slashes and aren't files
    we need to verify.
    """
    return "/" in command


def _resolve(command: str, project_dir: Path) -> Path:
    """Expand ``$CLAUDE_PROJECT_DIR/`` / ``~`` and resolve to an absolute path.

    Mirrors how Claude Code resolves a hook command path at execution
    time: ``$CLAUDE_PROJECT_DIR`` → project root; a leading ``~`` → the
    user's home dir (the shell expands tilde when it runs the hook);
    absolute paths as-is; a bare relative path joined onto ``project_dir``.

    HATS-594: a home-relative command (``~/.tmux/.../hook.sh``) is NOT
    absolute, so without :meth:`~pathlib.Path.expanduser` it was joined
    onto ``project_dir`` (``<project>/~/...``), which never exists — a
    false "not found" that refused otherwise-valid bumps. Expanding ``~``
    here makes the guard agree with the runtime.
    """
    rel = strip_claude_project_dir(command)
    if rel != command:
        return (project_dir / rel).resolve()
    p = Path(command).expanduser()
    if p.is_absolute():
        return p
    return (project_dir / command).resolve()


def _walk_hook_commands(data: object) -> list[tuple[str, str]]:
    """Walk a parsed settings.json ``hooks`` dict and yield (event, command).

    Settings shape:

    .. code-block:: json

        {
          "hooks": {
            "PreToolUse": [
              {"matcher": "Bash", "hooks": [{"type": "command", "command": "..."}]}
            ]
          }
        }

    Only event keys in :data:`_HOOK_EVENT_KEYS` are walked; unknown
    keys are silently skipped (future-proofing if Claude Code adds new
    event types).
    """
    out: list[tuple[str, str]] = []
    if not isinstance(data, dict):
        return out
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for event, matchers in hooks.items():
        if event not in _HOOK_EVENT_KEYS:
            continue
        if not isinstance(matchers, list):
            continue
        for matcher in matchers:
            if not isinstance(matcher, dict):
                continue
            entries = matcher.get("hooks")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                command = entry.get("command")
                if isinstance(command, str) and command:
                    out.append((event, command))
    return out


def find_broken_hook_refs(project_dir: Path) -> list[BrokenHookRef]:
    """Scan every settings target and return refs whose path doesn't resolve.

    Returns an empty list when all hooks resolve OR when no settings
    file is present. Malformed settings.json (JSON parse error,
    permission failure) is treated as "no findings" — those are not
    HATS-549's problem to surface.
    """
    broken: list[BrokenHookRef] = []
    for rel in SETTINGS_TARGETS:
        settings_path = project_dir / rel
        if not settings_path.is_file():
            continue
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        for event, command in _walk_hook_commands(data):
            if not _looks_like_path(command):
                continue  # shell command, not a file ref
            resolved = _resolve(command, project_dir)
            if resolved.is_file():
                continue
            broken.append(BrokenHookRef(
                settings_file=rel,
                event=event,
                command=command,
                resolved_path=resolved,
            ))
    return broken


def assert_runtime_hooks_resolve(
    project_dir: Path,
    *,
    backup_path: Path | None = None,
) -> None:
    """Raise :class:`AssemblyError` if any hook command path is missing.

    Called as the LAST step of every install-time path:

    - ``cli/assembly.py::do_bump`` after ``_refresh`` + ``_run_diagnostics``
    - ``cli/assembly.py::self_init`` after ``asm.init`` (non-greenfield)
    - ``cli/maintenance.py`` self-update in-process branch after the
      compose-refresh-diagnose block

    Args:
        project_dir: Project root.
        backup_path: Path returned by :func:`migration_backup.snapshot_pre_bump`
            for this bump session. When provided, the error message includes
            a ``tar -xzf`` recovery one-liner targeting it. ``None`` is
            tolerated (e.g. ``AI_HATS_BUMP_BACKUP_DIR=-`` hard-disable mode)
            — the diagnosis is still emitted, minus the recovery hint.

    Raises:
        AssemblyError: when one or more hook command paths don't resolve.
            The message lists each broken entry with ``(file, event,
            command, resolved-target)`` and — when ``backup_path`` is set —
            a recovery one-liner.
    """
    broken = find_broken_hook_refs(project_dir)
    if not broken:
        return

    lines = [
        f"{len(broken)} hook command path(s) in .claude/settings.json "
        "do not resolve to an existing file. Claude Code will print "
        "'No such file or directory' on every matching tool call.",
        "",
    ]
    for ref in broken:
        try:
            rel_resolved = ref.resolved_path.relative_to(project_dir).as_posix()
        except ValueError:
            rel_resolved = str(ref.resolved_path)
        lines.append(
            f"  {ref.settings_file} | {ref.event} | {ref.command}"
        )
        lines.append(f"    → not found at: {rel_resolved}")
    if backup_path is not None:
        lines.append("")
        lines.append(
            f"Recovery: tar -xzf {backup_path} -C {project_dir}"
        )
        lines.append(
            "  (restores the project tree to its pre-bump state; then "
            "either remove the broken hook entry from settings.json or "
            "restore the missing script before re-running bump)."
        )
    raise AssemblyError("\n".join(lines))
