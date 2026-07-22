#!/usr/bin/env python3
"""HATS-857/HATS-889 — worktree-isolation PreToolUse gate. Denies on trigger, fails open.

Hard-deny an Edit/Write to a code/config file in the MAIN checkout instead of a worktree
(main-checkout edits collide, HATS-526; a nudge here was ignored, PROX-375). A triggering,
non-gitignored file in MAIN -> exit 0 + permissionDecision "deny" (binds headless too,
unlike "ask"); else exit 0 silent. Recovery + discipline: SKILL.md. Kill switch:
AI_HATS_WT_GATE_OFF=1. Stdlib-only (system python3 via shebang, inline git). Zero egress.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_KILL_SWITCH = "AI_HATS_WT_GATE_OFF"
_EXTS_ENV = "AI_HATS_WT_GATE_EXTS"
_EXTS_FILENAME = "code_extensions.json"
# Canonical skill-source location of the extensions file, relative to a git repo
# root — lets the flattened ``library/hooks/`` copy still pick up project edits.
_SKILL_EXTS_RELPATH = (
    "library/core/skills/worktree-isolation/hooks/" + _EXTS_FILENAME
)

# Embedded mirror of ``code_extensions.json`` — the production fallback used once
# the engine flattens this script away from its sibling. Keep in sync with the
# JSON (asserted by tests/test_wt_gate_wiring.py).
_DEFAULT_LANGS = {
    "python": (".py", ".pyi", ".pyx"),
    "shell": (".sh", ".bash", ".zsh"),
    "go": (".go",),
    "rust": (".rs",),
    "ruby": (".rb",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "typescript": (".ts", ".tsx"),
    "web": (".vue", ".svelte"),
    "jvm": (".java", ".kt", ".scala", ".groovy"),
    "c_cpp": (".c", ".h", ".cc", ".cpp", ".hpp", ".cxx"),
    "csharp": (".cs",),
    "php": (".php",),
    "swift": (".swift",),
    "lua": (".lua",),
    "perl": (".pl", ".pm"),
    "elixir": (".ex", ".exs"),
    "clojure": (".clj", ".cljs"),
    "dart": (".dart",),
    "config": (".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".env"),
}

_DENY_REASON = (
    "GUARDRAIL (worktree-isolation): blocked — code/config edit in the MAIN checkout. "
    "Work in a worktree instead; see the worktree-isolation skill for how."
)


def _read_exts_json(path: Path) -> frozenset[str] | None:
    """Parse a ``{language: [".ext", ...]}`` map into a flat extension set.

    Non-list values (e.g. the ``_comment`` key) are ignored. Returns None on any
    read/parse error or an empty result, so the caller falls through to the next
    source."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    exts = {
        e
        for v in data.values()
        if isinstance(v, list)
        for e in v
        if isinstance(e, str) and e.startswith(".")
    }
    return frozenset(exts) or None


def _load_extensions(repo_root: Path | None) -> frozenset[str]:
    """Triggering extensions, first resolvable source wins (see module docstring)."""
    candidates: list[Path] = []
    env = os.environ.get(_EXTS_ENV)
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(Path(__file__).resolve().parent / _EXTS_FILENAME)
    if repo_root is not None:
        candidates.append(repo_root / _SKILL_EXTS_RELPATH)
    for path in candidates:
        exts = _read_exts_json(path)
        if exts:
            return exts
    return frozenset(e for exts in _DEFAULT_LANGS.values() for e in exts)


def _nearest_existing_dir(file_path: str) -> str | None:
    """The file's parent walked up to the nearest existing ancestor (a Write may
    target a not-yet-created file/dir). None if nothing resolvable."""
    try:
        d = Path(file_path).expanduser().parent
    except (OSError, ValueError):
        return None
    while not d.exists() and d != d.parent:
        d = d.parent
    return str(d) if d.exists() else None


def _git_info(directory: str) -> tuple[str, Path | None, Path | None]:
    """Classify `directory` as ('main'|'linked'|'nongit', repo_toplevel, common_dir).

    One ``git rev-parse`` (HATS-490): 'main' iff a git work tree whose
    --git-dir == --git-common-dir; 'linked' iff they differ; 'nongit' on any
    error (fail-safe). The resolved --git-common-dir is the repo *identity* shared
    across a repo's main checkout and all its linked worktrees — the session-scope
    key (HATS-959). Mirrors WorktreeManager.is_inside_linked_worktree inline
    because the hook runs under the system interpreter without ai_hats."""
    try:
        result = subprocess.run(
            [  # noqa: S607
                "git", "rev-parse", "--path-format=absolute",
                "--show-toplevel", "--git-dir", "--git-common-dir",
            ],
            cwd=directory,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return ("nongit", None, None)
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if len(lines) != 3:
        return ("nongit", None, None)
    toplevel, git_dir, common_dir = lines
    common = Path(common_dir).resolve()
    loc = "linked" if Path(git_dir).resolve() != common else "main"
    return (loc, Path(toplevel), common)


def _is_git_ignored(file_path: str, directory: str) -> bool:
    """True iff git ignores ``file_path``. Gitignored files (``.agent/`` tracker,
    ``.claude/``, ``ai-hats.yaml``, ``.venv/`` ...) are not version-controlled source,
    so a main-checkout edit of one cannot cause the cross-worktree collision the gate
    prevents — and tracker/config edits are *required* from the main repo (HATS-889).
    Fail-open: any git error -> True (treat as ignored -> silent), matching the hook's
    overall fail-open stance."""
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "check-ignore", "-q", "--", file_path],  # noqa: S607
            cwd=directory,
            capture_output=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return True
    return result.returncode != 1  # 0 = ignored; 1 = not ignored; other = fail-open


def main() -> int:
    if os.environ.get(_KILL_SWITCH) == "1":
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0  # unparsable / empty -> fail-open allow

    # Dual-payload parsing:
    # Claude Code sends arguments in `tool_input`.
    # Agy (Antigravity CLI) sends arguments in `toolCall.args`.
    # We check both to ensure the hook works across both surfaces.
    tool_input = payload.get("tool_input")
    if not tool_input:
        tool_call = payload.get("toolCall") or {}
        tool_input = tool_call.get("args") or {}
    
    file_path = (
        tool_input.get("file_path")
        or tool_input.get("path")
        or tool_input.get("target_file")
        or tool_input.get("TargetFile")
        or tool_input.get("AbsolutePath")
        or ""
    )
    if not file_path:
        return 0

    directory = _nearest_existing_dir(file_path)
    if directory is None:
        return 0  # unresolvable path -> silent
    location, repo_root, file_common = _git_info(directory)
    if location != "main":
        return 0  # non-git path or already inside a linked worktree -> silent

    # HATS-959: a main-checkout file in a DIFFERENT repo than the session cwd (e.g.
    # ~/dotfiles) is outside this project's worktree discipline -> silent. Key on
    # --git-common-dir (shared main+worktrees); unresolved cwd falls back to old deny.
    cwd = (payload.get("cwd") or "").strip()
    if cwd:
        _, _, session_common = _git_info(cwd)
        if session_common is not None and file_common != session_common:
            return 0  # file belongs to another repo than the session -> silent

    if Path(file_path).suffix not in _load_extensions(repo_root):
        return 0  # docs / non-triggering file -> silent

    if _is_git_ignored(file_path, directory):
        return 0  # gitignored tracker/runtime/config (.agent/, ai-hats.yaml) -> silent

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": _DENY_REASON,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
