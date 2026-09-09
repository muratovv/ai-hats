#!/usr/bin/env python3
"""Render ADR-0023's stage and gate tables from the code that defines them.

The inventory a reader used to find in the ADR — which stage exists, what it
checks, which gate requires it, where a gate stands — rotted seven stages out of
twenty-three while the code moved on. It now comes from what is already the
truth: `scripts/gates.sh list` (stage, description), each gate script's
`--stages` (what it requires), the maintainer role's `composition.apps` rows
(where each card gate is bound) and the quality-gate skill's `git_hooks` (where
the push gate stands). `--check` refuses a document that no longer matches;
the prose around the tables stays prose, because the decisions in it are not
derivable.
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
SKILL_RELPATH = "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/quality-gate"

STAGES_MARK = "gate-table:stages"
GATES_MARK = "gate-table:gates"


class SourceError(Exception):
    """A source the tables render from that cannot be read."""


@dataclass(frozen=True)
class Gate:
    name: str
    stages: tuple[str, ...]
    where: tuple[str, ...]
    #: `diff` if this gate also demands the zones the change touches, else `none`.
    zones: str


def _bash(repo: Path, script: str, *args: str) -> str:
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", str(repo / script), *args], capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        raise SourceError(
            f"{script} {' '.join(args)} exited {out.returncode}: {out.stderr.strip()}"
        )
    return out.stdout


def read_stages(repo: Path) -> list[tuple[str, str]]:
    rows = []
    for line in _bash(repo, GATES_SH, "list").splitlines():
        cells = [cell.strip() for cell in line.split("|", 1)]
        if len(cells) != 2:
            raise SourceError(f"{GATES_SH} list: a row is not `stage | description`: {line!r}")
        rows.append((cells[0], cells[1]))
    if not rows:
        raise SourceError(f"{GATES_SH} list printed nothing")
    return rows


def _walk_rows(node, trail: tuple[str, ...]):
    """Every binding row under `composition.apps`, with the app path above it."""
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and "run" in item:
                yield trail, item
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_rows(value, (*trail, str(key)))


def _frontmatter(text: str) -> str:
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SourceError("SKILL.md carries no frontmatter")
    return parts[1]


def read_gates(repo: Path) -> list[Gate]:
    """Every gate the role binds, in the role's order, then the git hooks'."""
    config = yaml.safe_load((repo / ROLE_RELPATH).read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    scripts: dict[str, str] = {}
    for trail, row in _walk_rows(config.get("composition", {}).get("apps", {}), ()):
        run = str(row["run"])
        if not run.startswith("quality-gate/"):
            continue
        name = Path(run).stem
        scripts[name] = run.split("/", 1)[1]
        points = ", ".join(str(point) for point in row.get("at", []))
        found.setdefault(name, []).append(f"`{'.'.join(trail)}`: `{points}`")
    skill = yaml.safe_load(_frontmatter((repo / SKILL_RELPATH / "SKILL.md").read_text("utf-8")))
    for event, hooks in (skill.get("ai_hats", {}).get("git_hooks") or {}).items():
        for script in hooks:
            text = (repo / SKILL_RELPATH / script).read_text(encoding="utf-8")
            match = re.search(r"^GATE='([^']+)'", text, re.M)
            if match:
                scripts[match.group(1)] = script
                found.setdefault(match.group(1), []).append(f"`git {event}`")
    gates = []
    for name, where in found.items():
        stages = _bash(repo, f"{SKILL_RELPATH}/{scripts[name]}", "--stages").split()
        zones = _bash(repo, f"{SKILL_RELPATH}/{scripts[name]}", "--zones").strip()
        gates.append(Gate(name, tuple(stages), tuple(where), zones))
    if not gates:
        raise SourceError("no gate is bound anywhere")
    return gates


def _table(headers: list[str], body: list[list[str]]) -> str:
    """A pipe table with padded columns — the shape the markdown formatter keeps."""
    widths = [max(len(cell) for cell in column) for column in zip(headers, *body, strict=True)]

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)) + " |"

    rule = "| " + " | ".join("-" * w for w in widths) + " |"
    return "\n".join([line(headers), rule, *(line(cells) for cells in body)])


def read_zones(repo: Path) -> dict[str, list[str]]:
    """`stage -> the path prefixes that demand it`, from `gates.sh zones`.

    A zone stage is required by NO gate's declaration and by every card gate that
    sees one of its prefixes in the diff. Rendering it like any other stage would
    print a row saying only `push-gate`, and the `gate-table` check would then
    enforce that half-truth.

    A zone spans as many rows as it has prefixes, so the value is a list: keeping
    one would name a single path and hide the rest, which is the same half-truth
    one level down.
    """
    zones: dict[str, list[str]] = {}
    for line in _bash(repo, GATES_SH, "zones").splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) != 3:
            raise SourceError(f"{GATES_SH} zones: a row is not `prefix | marker | stage`: {line!r}")
        prefix, _marker, stage = cells
        zones.setdefault(stage, []).append(prefix)
    return zones


def render_stages(stages: list[tuple[str, str]], gates: list[Gate], zones: dict[str, list[str]]) -> str:
    body = []
    for stage, desc in stages:
        required = " ".join(g.name for g in gates if stage in g.stages)
        if stage in zones:
            prefixes = ", ".join(f"`{prefix}`" for prefix in zones[stage])
            asking = " ".join(g.name for g in gates if g.zones == "diff")
            by_diff = f"{asking}, когда дифф трогает {prefixes}"
            required = f"{required}; {by_diff}" if required else by_diff
        body.append([f"`{stage}`", required or "-", desc])
    return _table(["стадия", "требуют гейты", "что проверяет"], body)


def render_gates(gates: list[Gate]) -> str:
    body = [[f"`{g.name}`", "; ".join(g.where), " ".join(g.stages)] for g in gates]
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
    stages, gates, zones = read_stages(repo), read_gates(repo), read_zones(repo)
    doc = splice(doc, STAGES_MARK, render_stages(stages, gates, zones))
    return splice(doc, GATES_MARK, render_gates(gates))


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
        print(f"[{CHECK}] {doc_path} matches the stages and the gates", file=sys.stderr)
        return 0
    print(
        f"[{CHECK}] {doc_path} is stale — run `python scripts/gen_gate_table.py --write`",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
