#!/usr/bin/env python3
"""Render ADR-0023's stage and gate tables from the code that defines them.

The inventory a reader used to find in the ADR — which stage exists, which gate
requires it, what it checks, where a gate stands — rotted seven stages out of
twenty-three while the code moved on. It now comes from three places that are
already the truth: `scripts/gates.sh table` (stage, gates, description), the
maintainer role's `composition.apps` rows (where each card gate is bound, by its
`gate:` cargo) and the quality-gate skill's `git_hooks` (where the push gate
stands). `--check` refuses a document that no longer matches; the ADR's prose
around the tables stays prose, because the decisions in it are not derivable.

What `--check` cannot catch is a sentence in the prose drifting from the table
beside it — that half is still a human's.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

CHECK = "gate-table"
DOC_RELPATH = "docs/adr/0023-quality-gate.md"
GATES_SH = "scripts/gates.sh"
ROLE_RELPATH = (
    "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/roles/maintainer/config.yaml"
)
SKILL_RELPATH = (
    "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/maintainer-quality-gate"
)

STAGES_MARK = "gate-table:stages"
GATES_MARK = "gate-table:gates"


class SourceError(Exception):
    """A source the tables render from that cannot be read."""


@dataclass(frozen=True)
class Row:
    stage: str
    gates: tuple[str, ...]
    description: str


def read_rows(repo: Path) -> list[Row]:
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(repo / GATES_SH), "table"], capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        raise SourceError(f"{GATES_SH} table exited {out.returncode}: {out.stderr.strip()}")
    rows = []
    for line in out.stdout.splitlines():
        cells = [cell.strip() for cell in line.split("|", 2)]
        if len(cells) != 3:
            raise SourceError(f"{GATES_SH}: a row is not `stage | gates | description`: {line!r}")
        rows.append(Row(cells[0], tuple(cells[1].split()), cells[2]))
    if not rows:
        raise SourceError(f"{GATES_SH} table printed nothing")
    return rows


def roster(rows: list[Row]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        for gate in row.gates:
            if gate != "-" and gate not in seen:
                seen.append(gate)
    return seen


def _walk_rows(node, trail: tuple[str, ...]):
    """Every binding row under `composition.apps`, with the app path above it."""
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and "run" in item:
                yield trail, item
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_rows(value, (*trail, str(key)))


def read_bindings(repo: Path) -> dict[str, list[str]]:
    """`gate -> ["rack.tasks: ->review", ...]` from the role, plus the git hooks."""
    config = yaml.safe_load((repo / ROLE_RELPATH).read_text(encoding="utf-8"))
    bindings: dict[str, list[str]] = {}
    for trail, row in _walk_rows(config.get("composition", {}).get("apps", {}), ()):
        gate = row.get("gate")
        if not gate:
            continue
        points = ", ".join(str(point) for point in row.get("at", []))
        bindings.setdefault(str(gate), []).append(f"`{'.'.join(trail)}`: `{points}`")
    skill = yaml.safe_load(_frontmatter((repo / SKILL_RELPATH / "SKILL.md").read_text("utf-8")))
    for event, scripts in (skill.get("ai_hats", {}).get("git_hooks") or {}).items():
        for script in scripts:
            text = (repo / SKILL_RELPATH / script).read_text(encoding="utf-8")
            match = re.search(r"^GATE='([^']+)'", text, re.M)
            if match:
                bindings.setdefault(match.group(1), []).append(f"`git {event}`")
    return bindings


def _frontmatter(text: str) -> str:
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SourceError("SKILL.md carries no frontmatter")
    return parts[1]


def _table(headers: list[str], body: list[list[str]]) -> str:
    """A pipe table with padded columns — the shape the markdown formatter keeps."""
    widths = [max(len(cell) for cell in column) for column in zip(headers, *body, strict=True)]

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)) + " |"

    rule = "| " + " | ".join("-" * w for w in widths) + " |"
    return "\n".join([line(headers), rule, *(line(cells) for cells in body)])


def render_stages(rows: list[Row]) -> str:
    body = [[f"`{r.stage}`", " ".join(r.gates), r.description] for r in rows]
    return _table(["стадия", "требуют гейты", "что проверяет"], body)


def render_gates(rows: list[Row], bindings: dict[str, list[str]]) -> str:
    body = []
    for gate in roster(rows):
        stages = " ".join(r.stage for r in rows if gate in r.gates)
        where = "; ".join(bindings.get(gate, [])) or "—"
        body.append([f"`{gate}`", where, stages])
    return _table(["гейт", "где применяется", "стадии"], body)


def splice(doc: str, mark: str, table: str) -> str:
    """Replace what stands between `<!-- mark -->` and `<!-- /mark -->`."""
    # An EMPTY block (the two markers on adjacent lines) is a legal starting point.
    pattern = re.compile(
        rf"(<!-- {re.escape(mark)} -->)(?:\n.*?)?\n(<!-- /{re.escape(mark)} -->)", re.S
    )
    if not pattern.search(doc):
        raise SourceError(f"the document carries no `<!-- {mark} -->` … `<!-- /{mark} -->` block")
    # Blank lines around the table: the markdown formatter inserts them between an
    # HTML comment and a table, and the rendered text must be what it leaves.
    return pattern.sub(lambda m: f"{m.group(1)}\n\n{table}\n\n{m.group(2)}", doc, count=1)


def render(repo: Path, doc: str) -> str:
    rows = read_rows(repo)
    doc = splice(doc, STAGES_MARK, render_stages(rows))
    return splice(doc, GATES_MARK, render_gates(rows, read_bindings(repo)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the tables in place")
    mode.add_argument("--check", action="store_true", help="fail if the tables are stale")
    parser.add_argument("--doc", default=None, help=f"the document (default: {DOC_RELPATH})")
    args = parser.parse_args(argv)

    doc_path = Path(args.doc) if args.doc else REPO_ROOT / DOC_RELPATH
    current = doc_path.read_text(encoding="utf-8")
    try:
        rendered = render(REPO_ROOT, current)
    except SourceError as exc:
        print(f"[{CHECK}] BROKEN: {exc}", file=sys.stderr)
        return 3
    if args.write:
        if rendered != current:
            doc_path.write_text(rendered, encoding="utf-8")
        print(f"[{CHECK}] {doc_path} current", file=sys.stderr)
        return 0
    if rendered == current:
        print(f"[{CHECK}] {doc_path} matches scripts/gates.sh and the bindings", file=sys.stderr)
        return 0
    print(
        f"[{CHECK}] {doc_path} is stale — run `python scripts/gen_gate_table.py --write`",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
