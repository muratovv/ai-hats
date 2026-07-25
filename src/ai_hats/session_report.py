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
                }
                for e in self.plan.entries
            ],
            "duplicates": [str(p) for p in self.plan.duplicates()],
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
        lines += [
            "",
            "launch    " + " ".join(d["launch"]),
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

        return "\n".join(lines) + "\n"
