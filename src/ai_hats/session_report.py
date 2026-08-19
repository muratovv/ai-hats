"""The dry-run report — a rendering of the materialization record (HATS-1211).

Both views come from one dict, so the human table and ``--json`` cannot disagree.
Env is reported by key name only: values carry secrets and ``--json`` output ends
up in fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .materialization import MaterializationPlan
from .session_artifacts import SessionPolicy

if TYPE_CHECKING:  # pragma: no cover — typing only
    from ai_hats_core import ConsentPoint

    from .check_snapshot import ReportedCheck


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def consent_entry(consent) -> dict:
    """One consent declaration as the guard reads it (HATS-1719).

    Public because it is the ONE writer of this shape: the guard on the other
    side reads fields, and a second place building them by hand is how the two
    drift until the question quietly stops being asked.

    The ends travel ALREADY PARSED. The guard is stdlib-only and cannot import
    the rack's parser, so it used to cut the name itself — and on an arrow that
    cut returned nothing, which disarmed the question on BOTH roads into master
    without a word (the HATS-1682 A5 class). Fields, not grammar.
    """
    from .check_points import selector_ends

    source, target = selector_ends(consent.app, consent.selector)
    return {
        "app": consent.app,
        "path": list(consent.path),
        "selector": consent.selector,
        "from": source,
        "to": target,
        "declared_by": consent.declared_by,
    }


def _where(check: dict) -> str:
    """The app and points a row binds, as one column of the launch report."""
    at = ",".join(check.get("at") or []) or "-"
    return f"{check['app']}:{at}"


@dataclass(frozen=True)
class SessionReport:
    role: str
    provider: str
    run_mode: str
    policy: SessionPolicy
    launch: list[str]
    env: dict[str, str]
    prompt: Path | None
    plan: MaterializationPlan
    cwd: str = ""
    # Render-only, and deliberately outside to_dict(): the body was never in the
    # payload, and a plan-mode build has no file for --dry-run-full to read.
    prompt_text: str | None = None
    # Paths that appeared on disk during a plan-mode build: a write that went
    # around the port. Empty is the invariant; non-empty names a live bypass.
    escapes: tuple[Path, ...] = ()
    # Known gaps between what this report can observe and what the surface
    # actually delivers — never leave such a gap silent.
    notes: tuple[str, ...] = ()
    # HATS-1548: the gates this launch arms. Not derivable from the plan — the
    # skill mirror a check runs from is written per SKILL, not per binding.
    checks: tuple[ReportedCheck, ...] = ()
    #: HATS-1682: where this role wants the supervisor asked. The guard on the
    #: tool call reads it from here — a role property reaching the surface the
    #: way every other one does, through the session's own envelope.
    consent: tuple[ConsentPoint, ...] = ()

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "provider": self.provider,
            "run_mode": self.run_mode,
            "cwd": self.cwd,
            "policy": {
                "context": self.policy.context,
                "hooks": self.policy.hooks,
                "settings": self.policy.settings,
            },
            "launch": list(self.launch),
            "env_keys": sorted(self.env),  # names only — values carry secrets
            "prompt": str(self.prompt) if self.prompt else None,
            "materialized": [
                {
                    "kind": e.kind.value,
                    "target": str(e.target),
                    "source": str(e.source) if e.source else None,
                    "size": e.size,
                    "file_count": e.file_count,
                    "detail": e.detail,
                    "digest": e.digest,
                }
                for e in self.plan.entries
            ],
            "duplicates": [str(p) for p in self.plan.duplicates()],
            "checks": [
                {
                    "skill": c.binding.skill,
                    "script": c.binding.script,
                    "app": c.binding.app,
                    "at": list(c.binding.at),
                    "on_error": c.binding.on_error,
                    "declared_by": c.binding.declared_by,
                    "runs_from": str(c.runs_from) if c.runs_from else None,
                    "planned": c.planned,
                }
                for c in self.checks
            ],
            "consent": [consent_entry(c) for c in self.consent],
            "escapes": [str(p) for p in self.escapes],
            "notes": list(self.notes),
        }

    def render(self, *, full: bool = False) -> str:
        d = self.to_dict()
        lines = [
            f"role      {d['role']} -> {d['provider']}   run_mode={d['run_mode']}",
            "policy    " + " ".join(f"{k}={'on' if v else 'off'}" for k, v in d["policy"].items()),
        ]
        if d["cwd"]:
            lines.append(f"cwd       {d['cwd']}")
        # Both surfaces pass the whole role text as one argv token (claude's
        # system_prompt, agy's -p) — unreadable inline, verbatim in --json.
        shown = [
            t if len(t) <= 160 else f"{t[:80]}… <{_human_size(len(t.encode()))} total>"
            for t in d["launch"]
        ]
        lines += [
            "",
            "launch    " + " ".join(shown),
            "env       " + (", ".join(d["env_keys"]) or "(none)"),
        ]

        if d["prompt"]:
            lines += ["", f"prompt    {d['prompt']}"]
            if full:
                body = Path(d["prompt"])
                lines.append(
                    self.prompt_text
                    if self.prompt_text is not None
                    else (body.read_text() if body.is_file() else "(not written)")
                )

        lines += ["", "materialized"]
        if not d["materialized"]:
            lines.append("  (nothing)")
        for e in d["materialized"]:
            extra = f"  ({e['file_count']} files)" if e["kind"] == "copy_tree" else ""
            detail = f"  {e['detail']}" if e["detail"] else ""
            lines.append(
                f"  {e['kind']:<11} {e['target']}{extra}{detail}"
                + (f"  {_human_size(e['size'])}" if e["size"] else "")
            )

        for dup in d["duplicates"]:
            lines.append(f"  ! {dup} materialized twice")

        lines += ["", "checks"]
        if not d["checks"]:
            lines.append("  (none bound)")
        for c in d["checks"]:
            lines.append(
                f"  {_where(c):<20} {c['skill']}/{c['script']}"
                f"  on_error={c['on_error']}  by {c['declared_by']}"
            )
            # An armed gate is the quiet case; anything else is what the operator
            # came for, so only the unhappy branches get a second line.
            if c["runs_from"] is None:
                lines.append("    ! UNRESOLVED — this gate has no bytes to run (see notes)")
            elif not c["planned"]:
                lines.append(f"    ! {c['runs_from']} is NOT written by this launch")

        if d["notes"]:
            lines.append("")
            lines += [f"note      {n}" for n in d["notes"]]

        if d["escapes"]:
            lines += [
                "",
                "BYPASS — these were written for real during a dry-run, i.e. by a",
                "path that does not go through the materialization port (HATS-1207):",
            ]
            lines += [f"  ! {p}" for p in d["escapes"]]
            lines.append("  (removed again; the dry-run left nothing behind)")

        return "\n".join(lines) + "\n"
