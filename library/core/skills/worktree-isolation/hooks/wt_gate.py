#!/usr/bin/env python3
"""HATS-857/HATS-889 — worktree-isolation PreToolUse gate. Denies on trigger, fails open on error. Stdlib-only.

DENY an agent editing a code/config file in the MAIN checkout instead of an isolated
worktree (concurrent main-checkout edits collide — HATS-526). A nudge here was provably
ignored (HATS-889 / PROX-375), so the gate now hard-denies rather than advises.

Contract: stdin = Claude Code PreToolUse payload JSON (.tool_input.file_path). Triggering
extension AND file in the MAIN worktree (git-dir == git-common-dir) -> exit 0 +
{"hookSpecificOutput":{...,"permissionDecision":"deny","permissionDecisionReason":..}};
else exit 0 silent. `deny` is a final decision so it binds in BOTH interactive and
headless sessions (unlike `ask`, which degrades to `defer` with no UI). Sole escape:
supervisor-only AI_HATS_WT_GATE_OFF=1 — the agent MUST NOT self-set it (mirrors
AI_HATS_SHARED_STATE_ACK). Recovery recipe + discipline live in the SKILL.md.

Extensions grouped by language in code_extensions.json beside this script; resolution
$AI_HATS_WT_GATE_EXTS -> sibling -> <repo>/library/core/skills/worktree-isolation/hooks/
-> embedded _DEFAULT_LANGS mirror (a wiring test keeps JSON and mirror in sync). Stdlib-
only: the provider runs this under system python3 via shebang (no ai_hats import), so
worktree detection is an inline `git rev-parse`. Zero egress. Rationale: HATS-889 plan.md.
comment-length: allow
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
    "The worktree is the default; move to one and re-apply the edit — see the "
    "worktree-isolation skill (SKILL.md) for the recovery recipe. Do NOT retry or "
    "rephrase this edit, and do NOT self-set AI_HATS_WT_GATE_OFF; only the supervisor "
    "may authorize a direct-MAIN edit."
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


def _git_info(directory: str) -> tuple[str, Path | None]:
    """Classify `directory` as ('main'|'linked'|'nongit', repo_toplevel|None).

    One ``git rev-parse`` (HATS-490): 'main' iff a git work tree whose
    --git-dir == --git-common-dir; 'linked' iff they differ; 'nongit' on any
    error (fail-safe). Mirrors WorktreeManager.is_inside_linked_worktree inline
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
        return ("nongit", None)
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if len(lines) != 3:
        return ("nongit", None)
    toplevel, git_dir, common_dir = lines
    loc = "linked" if Path(git_dir).resolve() != Path(common_dir).resolve() else "main"
    return (loc, Path(toplevel))


def main() -> int:
    if os.environ.get(_KILL_SWITCH) == "1":
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0  # unparsable / empty -> fail-open allow

    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not file_path:
        return 0

    directory = _nearest_existing_dir(file_path)
    if directory is None:
        return 0  # unresolvable path -> silent
    location, repo_root = _git_info(directory)
    if location != "main":
        return 0  # non-git path or already inside a linked worktree -> silent

    if Path(file_path).suffix not in _load_extensions(repo_root):
        return 0  # docs / non-triggering file -> silent

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
