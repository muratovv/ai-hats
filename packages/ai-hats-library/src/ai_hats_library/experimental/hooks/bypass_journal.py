#!/usr/bin/env python3
"""HATS-1486 — single writer for bypass journal (shell delegates to python).

PreToolUse hooks are stdlib-only and run under the system interpreter, so they
cannot import ``ai_hats``; they import this sibling instead. Shell hooks invoke
this file via CLI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: Field order of every journal line.
FIELDS = (
    "ts",
    "event",
    "hook",
    "kind",
    "reason",
    "cmd",
    "head_before",
    "branch",
    "session_id",
    "sha",
)


#: Field order of every catch line. Deliberately NOT :data:`FIELDS`:
#: head_before/branch/sha are audit fields, cost two extra git calls on a hot
#: path, and stamp_sha never touches a catch (HATS-1634).
CATCH_FIELDS = (
    "ts",
    "event",
    "hook",
    "kind",
    "rule",
    "verdict",
    "cmd",
    "session_id",
)

#: Mirrors ``ai_hats_observe.artifacts.SESSION_PREFIX`` — hooks are stdlib-only
#: and cannot import it. Pinned by a contract test.
SESSION_PREFIX = "session_"

#: Mirrors ``runs_dir()``'s bootstrap default, relative to the project root.
RUNS_REL = "sessions/runs"
AI_HATS_REL = ".agent/ai-hats"


def _git(*args: str, cwd: Path | str | None = None) -> str:
    """Run a read-only git command; empty string when git cannot answer."""
    try:
        res = subprocess.run(  # noqa: S603 — argv is literal, never caller input
            ["git", *args],  # noqa: S607 — PATH lookup is the point in a hook
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def _git_common_dir(cwd: Path | str | None = None) -> Path | None:
    """The COMMON git dir as an absolute path, or None when git cannot answer.

    ``--git-common-dir`` answers relatively (``.git``) from a repo root, so it
    must be joined against the cwd it was asked about — not the process cwd.
    """
    raw = _git("rev-parse", "--git-common-dir", cwd=cwd)
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(cwd) / path if cwd is not None else Path.cwd() / path
    return path.resolve()


def session_id_from_hook_path(path: str) -> str:
    """Session id a materialized hook's own path encodes; '' when it does not.

    The harness invokes runtime hooks by absolute path inside the session tree —
    ``.../sessions/<session_id>/plugin/skills/<skill>/hooks/<hook>`` — so the
    path carries the attribution that the environment does not (HATS-1634).
    """
    if not path:
        return ""
    parts = Path(path).parts
    for idx in range(len(parts) - 2, 0, -1):
        if parts[idx] != "sessions":
            continue
        candidate = parts[idx + 1]
        if candidate in ("runs", "retros"):
            return ""
        return (
            candidate[len(SESSION_PREFIX) :] if candidate.startswith(SESSION_PREFIX) else candidate
        )
    return ""


def _journal_path(
    session_id: str, filename: str, cwd: Path | str | None = None
) -> tuple[Path | None, bool]:
    """Where a record belongs: (path, attributed_to_a_session).

    A record that knows its session lands beside that session's ``audit.md``, so
    everything about one session is in one directory. One that does not keeps the
    repo-wide journal under ``.git`` — the address every session-less git-tier
    row has always had. The session dir must EXIST: it is created at session
    start, so its absence means this is not that session's tree, and guessing
    would file the record under a session that never ran.
    """
    git_dir = _git_common_dir(cwd)
    if git_dir is None:
        return None, False
    if session_id:
        # From THIS repo, never AI_HATS_DIR: inherited from another checkout it
        # names a foreign tracker, and a record filed there is silent corruption.
        session_dir = git_dir.parent / AI_HATS_REL / RUNS_REL / f"{SESSION_PREFIX}{session_id}"
        if session_dir.is_dir():
            return session_dir / filename, True
    return git_dir / "ai-hats" / filename, False


def _resolve_session(session_id: str, hook_path: str) -> str:
    """The session a record belongs to, by the three channels that can know it.

    The path is what makes runtime attribution work at all: the harness spawns a
    PreToolUse hook with an environment that does not carry AI_HATS_SESSION_ID
    (measured: 5 of 3898 rows), but it invokes the hook by its absolute path
    inside the session tree, and that path names the session.
    """
    return (
        session_id
        or session_id_from_hook_path(hook_path or sys.argv[0])
        or os.environ.get("AI_HATS_SESSION_ID", "")
    )


def journal_catch(
    rule: str,
    verdict: str,
    *,
    hook: str | None = None,
    cmd: str = "",
    session_id: str = "",
    hook_path: str = "",
    cwd: Path | str | None = None,
) -> bool:
    """Append one record of a gate that FIRED. Returns False (loudly) if it could not.

    Never raises, for the same reason :func:`journal_bypass` does not: a gate must
    not die because its telemetry is unwritable.
    """
    own_path = hook_path or sys.argv[0]
    hook_name = hook or Path(own_path).name or "unknown"
    resolved_id = _resolve_session(session_id, own_path)

    path, attributed = _journal_path(resolved_id, "catches.jsonl", cwd)
    if path is None:
        print(
            f"[catch-journal] NOT RECORDED ({hook_name}/{rule}: {verdict}) — no git dir",
            file=sys.stderr,
        )
        return False

    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": os.environ.get("AI_HATS_HOOK_EVENT", "unknown"),
        "hook": hook_name,
        "kind": "catch",
        "rule": rule,
        "verdict": verdict,
        "cmd": cmd,
        "session_id": resolved_id if attributed else "",
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({k: entry[k] for k in CATCH_FIELDS}, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(
            f"[catch-journal] NOT RECORDED ({hook_name}/{rule}: {verdict}) — {path}: {exc}",
            file=sys.stderr,
        )
        return False
    if not attributed:
        print(
            f"[catch-journal] unattributed ({hook_name}/{rule}: {verdict}) — "
            f"no session dir for {resolved_id or '<no session id>'}; recorded in {path}",
            file=sys.stderr,
        )
    return True


def journal_bypass(
    kind: str,
    reason: str,
    *,
    hook: str | None = None,
    cmd: str = "",
    session_id: str = "",
    hook_path: str = "",
    cwd: Path | None = None,
) -> bool:
    """Append one bypass record. Returns False (loudly) if it could not.

    Never raises: a hook must not die because the journal is unwritable. It must
    not go quiet either — an unrecorded bypass is the defect this file removes.
    """
    own_path = hook_path or sys.argv[0]
    hook_name = hook or Path(own_path).name or "unknown"
    resolved_id = _resolve_session(session_id, own_path)
    path, _attributed = _journal_path(resolved_id, "bypasses.jsonl", cwd)
    if path is None:
        print(f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — no git dir", file=sys.stderr)
        return False

    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": os.environ.get("AI_HATS_HOOK_EVENT", "unknown"),
        "hook": hook_name,
        "kind": kind,
        "reason": reason,
        "cmd": cmd,
        "head_before": _git("rev-parse", "HEAD", cwd=cwd),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd),
        "session_id": resolved_id,
        "sha": "",
    }
    # PreToolUse hooks fire outside pre-commit, so sha is set immediately.
    # pre-commit event leaves sha empty for post-commit to stamp.
    entry["sha"] = "" if entry["event"] == "pre-commit" else entry["head_before"]

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({k: entry[k] for k in FIELDS}, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(
            f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — {path}: {exc}",
            file=sys.stderr,
        )
        return False
    return True


def _stamp_targets() -> list[Path]:
    """Every journal a post-commit can reach: the repo-wide one under ``.git``,
    and this session's own — a pre-commit bypass that knew its session was
    filed there, and stamping only ``.git`` would leave it unstamped forever."""
    out: list[Path] = []
    repo_wide, _ = _journal_path("", "bypasses.jsonl")
    if repo_wide is not None:
        out.append(repo_wide)
    session_id = _resolve_session("", "")
    if session_id:
        mine, attributed = _journal_path(session_id, "bypasses.jsonl")
        if attributed and mine is not None:
            out.append(mine)
    return out


def stamp_sha() -> bool:
    """Stamp the new commit SHA onto pre-commit entries waiting in the journals."""
    head = _git("rev-parse", "HEAD")
    if not head:
        return True
    parent = _git("rev-parse", "HEAD^")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    ok = True
    for path in _stamp_targets():
        ok = _stamp_one(path, head, parent, branch) and ok
    return ok


def _stamp_one(path: Path, head: str, parent: str, branch: str) -> bool:
    """Stamp ``head`` onto the rows of one journal that are waiting for it."""
    if not path.is_file():
        return True

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[bypass-journal] sha NOT STAMPED for {head} — {path}: {exc}", file=sys.stderr)
        return False

    lines = raw_text.splitlines(keepends=True)
    new_lines: list[str] = []
    modified = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue
        try:
            data = json.loads(stripped)
        except Exception:
            # Preserve unparseable lines verbatim
            new_lines.append(line)
            continue

        if isinstance(data, dict) and data.get("sha") == "":
            hb = data.get("head_before", "")
            br = data.get("branch", "")
            # br == "" is the first commit: pre-commit ran with no HEAD, so it
            # could not name a branch. head_before still pins it.
            if hb == parent and (br == branch or br == ""):
                data["sha"] = head
                new_line = (
                    json.dumps({k: data.get(k, "") for k in FIELDS}, ensure_ascii=False) + "\n"
                )
                new_lines.append(new_line)
                modified = True
                continue
        new_lines.append(line)

    if not modified:
        return True

    tmp_path: Path | None = None
    try:
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=path.parent, prefix="ai-hats-bypass-", suffix=".tmp"
        )
        tmp_path = Path(tmp_path_str)
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.writelines(new_lines)
        os.replace(tmp_path, path)
    except OSError as exc:
        print(f"[bypass-journal] sha NOT STAMPED for {head} — {path}: {exc}", file=sys.stderr)
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()  # safe-delete: ok ephemeral-tmp
            except OSError:
                pass

        return False

    return True


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="AI-HATS bypass journal writer")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    record_p = subparsers.add_parser("record")
    record_p.add_argument("--kind", required=True)
    record_p.add_argument("--reason", required=True)
    record_p.add_argument("--hook", required=True)
    record_p.add_argument("--hook-path", dest="hook_path", default="")
    record_p.add_argument("--cmd", default="")
    record_p.add_argument("--session-id", dest="session_id", default="")

    catch_p = subparsers.add_parser("catch")
    catch_p.add_argument("--rule", required=True)
    catch_p.add_argument("--verdict", required=True)
    catch_p.add_argument("--hook", required=True)
    catch_p.add_argument("--hook-path", dest="hook_path", default="")
    catch_p.add_argument("--cmd", default="")
    catch_p.add_argument("--session-id", dest="session_id", default="")

    subparsers.add_parser("stamp")

    args = parser.parse_args()
    if args.subcommand == "catch":
        ok = journal_catch(
            args.rule,
            args.verdict,
            hook=args.hook,
            hook_path=args.hook_path,
            cmd=args.cmd,
            session_id=args.session_id,
        )
        sys.exit(0 if ok else 1)
    elif args.subcommand == "record":
        ok = journal_bypass(
            args.kind,
            args.reason,
            hook=args.hook,
            hook_path=args.hook_path,
            cmd=args.cmd,
            session_id=args.session_id,
        )
        sys.exit(0 if ok else 1)
    elif args.subcommand == "stamp":
        ok = stamp_sha()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _main()
