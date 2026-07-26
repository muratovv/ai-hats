"""The dry-run report — a rendering of the materialization record (HATS-1211).

Both views come from one dict, so the human table and ``--json`` cannot disagree.
Env is reported by key name only: values carry secrets and ``--json`` output ends
up in fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .materialization import MaterializationPlan
from .session_artifacts import SessionPolicy


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


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
    # Paths that appeared on disk during a plan-mode build: a write that went
    # around the port. Empty is the invariant; non-empty names a live bypass.
    escapes: tuple[Path, ...] = ()
    # Known gaps between what this report can observe and what the surface
    # actually delivers — never leave such a gap silent.
    notes: tuple[str, ...] = ()

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
            "escapes": [str(p) for p in self.escapes],
            "notes": list(self.notes),
        }

    def render(self, *, full: bool = False) -> str:
        d = self.to_dict()
        lines = [
            f"role      {d['role']} -> {d['provider']}   run_mode={d['run_mode']}",
            "policy    " + " ".join(
                f"{k}={'on' if v else 'off'}" for k, v in d["policy"].items()
            ),
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
                lines.append(body.read_text() if body.is_file() else "(not written)")

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
