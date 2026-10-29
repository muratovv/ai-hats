#!/usr/bin/env python3
"""Every library SKILL.md description is one folded strip scalar.

The skill index pastes the parsed description into a single line. Clip (`>`)
keeps a trailing newline, so the path wraps; a quoted or plain line parses to
the same string and teaches the next edit a second spelling. The form that
holds both is `description: >-` with no newline in the value.

Style comes from the YAML node. Chomping is the indicator on the line that
node's mark names: PyYAML reports every fold as `>`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

LIBRARY_RELPATH = "packages/ai-hats-library/src/ai_hats_library"

CHECK = "skill-desc"

#: Samples the same judge must still classify. A checker that flags nothing,
#: or everything, fails here before it reports on the tree.
CONTROL_DOCS: tuple[tuple[str, bool], ...] = (
    ('---\nname: c\ndescription: "x"\n---\n', True),
    ("---\nname: c\ndescription: x\n---\n", True),
    ("---\nname: c\ndescription: 'x'\n---\n", True),
    ("---\nname: c\ndescription: >\n  x\n---\n", True),
    ("---\nname: c\ndescription: >+\n  x\n---\n", True),
    ("---\nname: c\ndescription: >-\n  x\n\n  y\n---\n", True),
    ("---\nname: c\ndescription: >-\n  see `a\n  b`\n---\n", True),
    ("---\nname: c\n---\n", True),
    ("---\nname: c\ndescription: >-\n  x y\n---\n", False),
    ("not frontmatter\n", True),
)

UNCOVERED = (
    "role and trait injections, and any frontmatter that is not a SKILL.md",
    "the skill body: only the description scalar is judged",
    "a skill outside this repository's library, including a consumer's libraries/",
    "line width: a folded strip may wrap anywhere a backtick span stays whole",
)


@dataclass(frozen=True)
class Finding:
    line: int
    message: str

    def render(self, path: str) -> str:
        return f"{path}:{self.line}: {self.message}"


def _frontmatter(text: str) -> tuple[str, int] | None:
    """The fence body and the 1-based line its first line occupies.

    None when the document has no closed leading fence.
    """
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), 2
    return None


def _indicator(block: str, mark_line: int) -> str:
    lines = block.splitlines()
    if mark_line < 0 or mark_line >= len(lines):
        return ""
    line = lines[mark_line]
    head, sep, tail = line.partition("description:")
    if not sep or head.strip():
        return ""
    token = tail.strip().split(None, 1)
    return token[0] if token else ""


def _form(style: str | None, indicator: str) -> str:
    if style == ">":
        return indicator or ">"
    if style == "|":
        return indicator or "|"
    if style == '"':
        return "double-quoted"
    if style == "'":
        return "single-quoted"
    if style is None:
        return "plain"
    return repr(style)


def _broken_backticks(block: str, node: yaml.Node) -> int | None:
    """A content line whose backticks do not close, as a 0-based block line."""
    if not isinstance(node, yaml.ScalarNode) or node.style != ">":
        return None
    lines = block.splitlines()
    end = min(node.end_mark.line, len(lines))
    for index in range(node.start_mark.line + 1, end):
        if lines[index].count("`") % 2 == 1:
            return index
    return None


def judge(text: str) -> list[Finding]:
    """Findings for one SKILL.md. Empty when the description is a one-line `>-`."""
    found = _frontmatter(text)
    if found is None:
        return [Finding(1, "no closed frontmatter fence")]
    block, origin = found
    try:
        node = yaml.compose(block) if block.strip() else None
    except yaml.YAMLError as exc:
        return [Finding(origin, f"invalid YAML: {exc}")]
    if not isinstance(node, yaml.MappingNode):
        return [Finding(origin, "frontmatter is not a mapping")]
    desc = next(
        (
            value
            for key, value in node.value
            if isinstance(key, yaml.ScalarNode) and key.value == "description"
        ),
        None,
    )
    if desc is None:
        return [Finding(origin, "no description")]
    line = origin + desc.start_mark.line
    if not isinstance(desc, yaml.ScalarNode):
        return [Finding(line, "description is not a string")]
    indicator = _indicator(block, desc.start_mark.line)
    problems: list[str] = []
    if indicator != ">-":
        problems.append(f"description is {_form(desc.style, indicator)}, want >-")
    value = desc.value
    if not isinstance(value, str) or not value.strip():
        problems.append("description is empty")
    elif "\n" in value:
        problems.append("description contains a newline, so the skill index wraps")
    broken = _broken_backticks(block, desc)
    if broken is not None:
        problems.append("a description line breaks inside backticks")
        line = origin + broken
    if problems:
        return [Finding(line, "; ".join(problems))]
    return []


def control_ok() -> bool:
    """True when every embedded sample is classified the way it is marked."""
    return all(bool(judge(text)) is expect for text, expect in CONTROL_DOCS)


def corpus(root: Path) -> list[Path]:
    lib = root / LIBRARY_RELPATH
    if not lib.is_dir():
        return []
    return sorted(path for path in lib.rglob("SKILL.md") if path.is_file())


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]).resolve() if argv else REPO_ROOT
    if not control_ok():
        print(
            f"[{CHECK}] BROKEN: the judge misclassified its own control sample. "
            "Nothing below is a verdict.",
            file=sys.stderr,
        )
        return 1

    lib = root / LIBRARY_RELPATH
    if not lib.is_dir():
        print(f"[{CHECK}] ok: no library at {LIBRARY_RELPATH}", file=sys.stderr)
        return 0

    files = corpus(root)
    if not files:
        print(
            f"[{CHECK}] BROKEN: {LIBRARY_RELPATH} has no SKILL.md. "
            "A clean report would prove the corpus was missed.",
            file=sys.stderr,
        )
        return 1

    findings: list[tuple[str, Finding]] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        findings.extend((rel, item) for item in judge(text))

    print(
        f"[{CHECK}] positive control: the judge still separates a folded strip "
        "from the other spellings",
        file=sys.stderr,
    )
    for rel, item in findings:
        print(f"[{CHECK}] FAIL: {item.render(rel)}", file=sys.stderr)
    for item in UNCOVERED:
        print(f"[{CHECK}] not covered: {item}", file=sys.stderr)
    if findings:
        print(
            f"[{CHECK}] {len(findings)} description(s) across {len(files)} SKILL.md "
            "— write description: >- with the value on one parsed line",
            file=sys.stderr,
        )
        return 1
    print(f"[{CHECK}] ok: {len(files)} SKILL.md descriptions are folded strips", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
