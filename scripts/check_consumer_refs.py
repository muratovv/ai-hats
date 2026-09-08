#!/usr/bin/env python3
"""A library component must not name a component that composes it.

A role saying why it takes a trait points DOWN; a trait naming the role that
takes it points UP, and has to be edited every time a role picks it up or drops
it. The composition graph settles which way a reference points, which is what
makes this checkable rather than a matter of taste.

Two surfaces, two strictnesses — see `scan_authoring` and `scan_prose`. The gate
carries its own positive control — see `control_hits`.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

LIBRARY_RELPATH = "packages/ai-hats-library/src/ai_hats_library"

CHECK = "consumer-refs"

#: The file that carries a component of each kind. Roles and traits declare
#: composition and are judged on their YAML comments; rules and skills are
#: shipped prose.
KIND_FILE = {
    "roles": "config.yaml",
    "traits": "config.yaml",
    "rules": "rule.md",
    "skills": "SKILL.md",
}
AUTHORING_KINDS = frozenset({"roles", "traits"})

OPT_OUT = f"{CHECK}: allow"
OPT_OUT_RE = re.compile(rf"{re.escape(OPT_OUT)}\b[ \t]*(?P<reason>[^\n>*#-]*)")

#: A name matches only whole: `judge` must not fire inside `role-judge`, and a
#: `dev::python` reference resolves to the component directory `python`.
NAME_RE_TEMPLATE = r"(?<![\w:-]){0}(?![\w-])"

#: What a provenance claim sounds like. Prose needs one of these NEXT TO a
#: carrier's name before it is a finding; a YAML comment needs none.
PHRASE_RE = re.compile(
    r"\b(?:carried|composed|consumed|used|installed|reached|reaches|wired|attached|bundled)"
    r"\s+(?:by|via|into)\b"
    r"|\bwho\s+gets\s+it\b"
    r"|\broles?\s+(?:that|which|who)\s+(?:compose|carry|use)\b"
    r"|\bwhichever\s+roles?\b",
    re.I,
)

#: The sample both detectors must still find on every run. It names a carrier
#: the way the two real shapes do, and it is scanned through the same code the
#: library goes through — see `control_hits`.
CONTROL_NAME = "control-role"
CONTROL_YAML = "composition:\n  # {name} composes this and should not be named\n  skills: []\n"
CONTROL_PROSE = "## Who gets it\n\nCarried by the `{name}` role.\n"

UNCOVERED = (
    "a consumer described rather than named — only a name the composition graph knows is judged",
    "a bare mention in shipped prose: a role-specific protocol skill has to be "
    "able to address its own role, so prose needs a relationship claim too",
    "a claim phrased outside `PHRASE_RE` — only the YAML half is settled by the "
    "graph alone; over prose this is a verb list, and a verb list is never done",
    "history in a comment — `git log -S` prose is `dev_rule_comment_discipline`'s "
    "half, not a machine's",
    "libraries outside this repository: this is a CI stage, not a shipped hook",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    carrier: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: `{self.carrier}` composes this {self.kind[:-1]}"


@dataclass(frozen=True)
class Component:
    kind: str
    name: str
    path: Path


def components(lib: Path) -> list[Component]:
    """Every component file in the library, layer by layer."""
    out: list[Component] = []
    for layer in sorted(p for p in lib.iterdir() if p.is_dir()):
        for kind, fname in KIND_FILE.items():
            directory = layer / kind
            if not directory.is_dir():
                continue
            out.extend(
                Component(kind, path.parent.name, path) for path in sorted(directory.rglob(fname))
            )
    return sorted(out, key=lambda c: c.path)


def carrier_map(found: list[Component]) -> tuple[dict[str, set[str]], list[str]]:
    """Component name -> every role/trait that reaches it, and any parse errors.

    The graph is two deep — a role composes a trait, a trait composes rules and
    skills, and traits do not nest — so one lift covers the closure.
    """
    direct: dict[str, set[str]] = {}
    errors: list[str] = []
    for component in found:
        if component.kind not in AUTHORING_KINDS:
            continue
        try:
            data = yaml.safe_load(component.path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            errors.append(f"{component.path}: {exc}")
            continue
        composition = (data.get("composition") or {}) if isinstance(data, dict) else {}
        refs = {
            ref.split("::")[-1]
            for key in ("traits", "rules", "skills")
            for ref in (composition.get(key) or [])
            if isinstance(ref, str)
        }
        direct.setdefault(component.name, set()).update(refs)

    carriers: dict[str, set[str]] = {}
    for consumer, composed in direct.items():
        for item in composed:
            carriers.setdefault(item, set()).add(consumer)
            for deeper in direct.get(item, ()):
                carriers.setdefault(deeper, set()).add(consumer)
    return carriers, errors


def comment_of(line: str) -> str | None:
    """The line's YAML comment, or None — a `#` inside quotes is not one."""
    quote: str | None = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[index:]
    return None


def yaml_comments(text: str) -> list[tuple[int, str]]:
    """(line number, comment) for real comments — block-scalar bodies excluded.

    An `injection: |` body is markdown, where `## SECTION` is a heading and not
    a comment at all.
    """
    out: list[tuple[int, str]] = []
    block_indent: int | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        if block_indent is not None:
            if stripped and indent <= block_indent:
                block_indent = None
            else:
                continue
        if not stripped:
            continue
        comment = comment_of(raw)
        if comment is not None:
            out.append((lineno, comment))
        body = raw[: len(raw) - len(comment)] if comment else raw
        if re.search(r":\s*[|>][-+0-9]*\s*$", body):
            block_indent = indent
    return out


def named_carriers(line: str, carriers: set[str]) -> list[str]:
    """The carriers this line names, longest name first."""
    return [
        name
        for name in sorted(carriers, key=len, reverse=True)
        if re.search(NAME_RE_TEMPLATE.format(re.escape(name)), line)
    ]


def allowed(line: str) -> str | None:
    """The reason this reference is not provenance, or None if it claims none."""
    match = OPT_OUT_RE.search(line)
    if not match:
        return None
    return match.group("reason").strip() or "(no reason given)"


def scan_authoring(text: str, carriers: set[str]) -> list[tuple[int, str, str | None]]:
    """(line, carrier, allowance) per carrier named in a YAML comment.

    A comment addresses no reader but the next author, so the bare name is the
    finding — the real violation this gate was written for carried no phrase.
    """
    hits = []
    for lineno, comment in yaml_comments(text):
        for name in named_carriers(comment, carriers):
            hits.append((lineno, name, allowed(comment)))
    return hits


def scan_prose(text: str, carriers: set[str]) -> list[tuple[int, str, str | None]]:
    """The same, but shipped prose must also make a relationship CLAIM.

    A protocol skill addresses its own role by name ("You were launched as
    `role-judge`") — that is a contract, and a bare-name rule here would refuse
    it along with the provenance.
    """
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not PHRASE_RE.search(line):
            continue
        for name in named_carriers(line, carriers):
            hits.append((lineno, name, allowed(line)))
    return hits


def control_hits() -> int:
    """What both detectors find in a sample built here, through the same code.

    A clean library proves nothing when the pattern itself is dead — a renamed
    directory, a changed composition key, a regex edit. This runs on every
    invocation and must return 2.
    """
    carriers = {CONTROL_NAME}
    return len(scan_authoring(CONTROL_YAML.format(name=CONTROL_NAME), carriers)) + len(
        scan_prose(CONTROL_PROSE.format(name=CONTROL_NAME), carriers)
    )


def main(argv: list[str] | None = None, *, control: Callable[[], int] = control_hits) -> int:
    root = Path(argv[0]).resolve() if argv else REPO_ROOT

    found_in_control = control()
    if found_in_control != 2:
        print(
            f"[{CHECK}] BROKEN: the detectors found {found_in_control} of 2 references in "
            f"their own control sample. Nothing below is a verdict.",
            file=sys.stderr,
        )
        return 1

    lib = root / LIBRARY_RELPATH
    if not lib.is_dir():
        print(f"[{CHECK}] ok: no library at {LIBRARY_RELPATH}", file=sys.stderr)
        return 0

    found = components(lib)
    carriers, errors = carrier_map(found)
    for error in errors:
        print(f"[{CHECK}] BROKEN: cannot read composition — {error}", file=sys.stderr)

    findings: list[Finding] = []
    allowances: list[str] = []
    for component in found:
        mine = carriers.get(component.name, set())
        if not mine:
            continue
        scan = scan_authoring if component.kind in AUTHORING_KINDS else scan_prose
        text = component.path.read_text(encoding="utf-8", errors="ignore")
        rel = component.path.relative_to(root).as_posix()
        for lineno, name, reason in scan(text, mine):
            if reason is not None:
                allowances.append(f"{rel}:{lineno}: {name} — {reason}")
            else:
                findings.append(Finding(rel, lineno, component.kind, name))

    findings.sort(key=lambda f: (f.path, f.line, f.carrier))
    for finding in findings:
        print(f"[{CHECK}] FAIL: {finding}", file=sys.stderr)
    for allowance in allowances:
        print(f"[{CHECK}] allowed — {allowance}", file=sys.stderr)

    scope = f"{len(found)} library component files"
    print(
        f"[{CHECK}] positive control: both detectors still find their own sample",
        file=sys.stderr,
    )

    if errors:
        return 1
    if findings:
        plural = "reference" if len(findings) == 1 else "references"
        print(f"[{CHECK}] {len(findings)} upward {plural} across {scope}", file=sys.stderr)
        print(
            f"[{CHECK}] move the WHY into the role that composes it; a reference that "
            f"must stay keeps its place with `{OPT_OUT} <why>` on the same line",
            file=sys.stderr,
        )
    else:
        print(f"[{CHECK}] ok: no component names its own carrier in {scope}", file=sys.stderr)
    for item in UNCOVERED:
        print(f"[{CHECK}] not covered: {item}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
