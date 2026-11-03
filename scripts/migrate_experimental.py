#!/usr/bin/env python3
"""Translate a shipped library root into its flat-form copy (ADR-0034 D6).

# comment-length: allow — this docstring is the script's --help text

    python scripts/migrate_experimental.py [--root <builtin_root>] [--only <composite>...]

Reads ``<root>/{core,usage,ai-hats-dev}`` in today's form and writes
``<root>/experimental/<layer>/`` in the flat form by the ADR-0034 D6 table:
a role or trait becomes ``<name>.yaml`` (``type: composite``) with
``<name>/prompt.md`` and ``<name>/priorities.md``, a rule becomes
``rules/<name>.md``, a skill tree is copied with ``ai_hats.type: skill`` in its
frontmatter. ``--only`` limits the run to the transitive closure of the named
composites.

Hook files come from the three frontmatter channels and from ``composition.apps``
(one file per point). The shared ``<root>/hooks`` payload is copied beside the
layers, and a skill symlink that stays inside the library root is kept so it
still resolves. The run is idempotent: same input, same bytes. A construct
outside the table refuses, naming the file — it is a decision for the ADR, not
a heuristic here.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LAYERS = ("core", "usage", "ai-hats-dev")
EXPERIMENTAL = "experimental"
SCHEMA_VERSION = 1

COMPOSITE_KEYS = frozenset({"name", "priorities", "composition", "injection"})
COMPOSITION_KEYS = frozenset({"traits", "rules", "skills", "apps"})
#: The frontmatter channels that become ``hook`` files (ADR-0034 D3, "skill").
DEFERRED_CHANNELS = ("git_hooks", "runtime_hooks", "worktree")
#: Whether a role is written for a person in a session or for a pipeline — a
#: human's decision at migration, never a heuristic (ADR-0034 D6).
RUN_MODE: dict[str, str] = {
    "hypothesis-intake": "automate",
    "maintainer": "hitl",
}
COPY_IGNORE = shutil.ignore_patterns("__pycache__", ".ruff_cache", "*.pyc")
WRAP = 78


class Refusal(Exception):
    """A construct the table does not cover, named by file."""


@dataclass(frozen=True)
class Composite:
    name: str
    layer: str
    kind: str  # roles | traits
    path: Path
    data: dict


@dataclass
class Report:
    written: list[Path] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.deferred.append(text)


# ── reading today's form ─────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise Refusal(f"{path}: not a mapping")
    return data


def read_composites(root: Path) -> dict[str, Composite]:
    found: dict[str, Composite] = {}
    for layer in LAYERS:
        for kind in ("roles", "traits"):
            base = root / layer / kind
            if not base.is_dir():
                continue
            for config in sorted(base.rglob("config.yaml")):
                name = "::".join(config.parent.relative_to(base).parts)
                data = _load_yaml(config)
                unknown = set(data) - COMPOSITE_KEYS
                if unknown:
                    raise Refusal(
                        f"{config}: unknown key(s) {sorted(unknown)} — not in ADR-0034 D6"
                    )
                composition = data.get("composition") or {}
                if not isinstance(composition, dict):
                    raise Refusal(f"{config}: composition is not a mapping")
                unknown = set(composition) - COMPOSITION_KEYS
                if unknown:
                    raise Refusal(
                        f"{config}: unknown composition key(s) {sorted(unknown)} — not in ADR-0034 D6"
                    )
                if name in found:
                    raise Refusal(
                        f"{config}: {name!r} is also {found[name].path} — one name, two files"
                    )
                found[name] = Composite(name, layer, kind, config, data)
    return found


def find_rule(root: Path, name: str) -> tuple[str, Path]:
    """Last-wins across layers, as the resolver searches today."""
    hit: tuple[str, Path] | None = None
    for layer in LAYERS:
        candidate = root / layer / "rules" / name.replace("::", "/") / "rule.md"
        if candidate.is_file():
            hit = (layer, candidate)
    if hit is None:
        raise Refusal(f"rule {name!r} is named but no layer holds rules/{name}/rule.md")
    return hit


def find_skill(root: Path, name: str) -> tuple[str, Path]:
    hit: tuple[str, Path] | None = None
    for layer in LAYERS:
        candidate = root / layer / "skills" / name.replace("::", "/")
        if (candidate / "SKILL.md").is_file():
            hit = (layer, candidate)
    if hit is None:
        raise Refusal(f"skill {name!r} is named but no layer holds skills/{name}/SKILL.md")
    return hit


# ── the closure ──────────────────────────────────────────────────────────────


@dataclass
class Closure:
    composites: dict[str, Composite]
    rules: dict[str, tuple[str, Path]]
    skills: dict[str, tuple[str, Path]]


def closure(root: Path, composites: dict[str, Composite], only: Iterable[str]) -> Closure:
    wanted = list(only) or sorted(composites)
    picked: dict[str, Composite] = {}
    rules: dict[str, tuple[str, Path]] = {}
    skills: dict[str, tuple[str, Path]] = {}
    queue = list(wanted)
    while queue:
        name = queue.pop(0)
        if name in picked:
            continue
        if name not in composites:
            raise Refusal(f"{name!r} is not a role or trait of any layer")
        comp = composites[name]
        picked[name] = comp
        composition = comp.data.get("composition") or {}
        for trait in composition.get("traits") or []:
            queue.append(trait)
        for rule in composition.get("rules") or []:
            rules.setdefault(rule, find_rule(root, rule))
        for skill in composition.get("skills") or []:
            skills.setdefault(skill, find_skill(root, skill))
    return Closure(picked, rules, skills)


# ── names in the flat form ───────────────────────────────────────────────────


def flat_names(closure: Closure) -> set[str]:
    names: set[str] = set()
    for name, comp in closure.composites.items():
        names.add(name)
        if (comp.data.get("injection") or "").strip():
            names.add(f"{name}::prompt")
        if comp.data.get("priorities"):
            names.add(f"{name}::priorities")
    names.update(f"rules::{r}" for r in closure.rules)
    names.update(f"skills::{s}" for s in closure.skills)
    return names


def spell(term: str, full: str, names: set[str]) -> str:
    """The term as the author wrote it while that suffix is unique; the full
    name where the flat tree makes the bare spelling ambiguous."""
    matches = [n for n in names if n == term or n.endswith("::" + term)]
    return term if matches == [full] else full


# ── writing the flat form ────────────────────────────────────────────────────


def _write(path: Path, text: str, report: Report) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_text() != text:
        path.write_text(text)
    report.written.append(path)


def _wrap_expr(terms: list[str]) -> str:
    lines: list[str] = []
    current = ""
    for i, term in enumerate(terms):
        piece = term if i == 0 else f"+ {term}"
        if current and len(current) + 1 + len(piece) > WRAP:
            lines.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    lines.append(current)
    return "\n".join(f"  {line}" for line in lines)


def write_composite(
    out: Path, comp: Composite, names: set[str], report: Report, hook_terms: list[tuple[str, str]]
) -> None:
    composition = comp.data.get("composition") or {}
    terms: list[str] = []
    if (comp.data.get("injection") or "").strip():
        terms.append(f"{comp.name}::prompt")
    if comp.data.get("priorities"):
        terms.append(f"{comp.name}::priorities")
    for trait in composition.get("traits") or []:
        terms.append(spell(trait, trait, names))
    for rule in composition.get("rules") or []:
        terms.append(spell(rule, f"rules::{rule}", names))
    for skill in composition.get("skills") or []:
        terms.append(spell(skill, f"skills::{skill}", names))
    for term, full in hook_terms:
        terms.append(spell(term, full, names))
    if not terms:
        raise Refusal(
            f"{comp.path}: nothing to compose — no injection, priorities, traits, rules or skills"
        )
    text = f"schema_version: {SCHEMA_VERSION}\ntype: composite\nexpr: >-\n{_wrap_expr(terms)}\n"
    if comp.kind == "roles":
        if comp.name not in RUN_MODE:
            raise Refusal(
                f"{comp.path}: role {comp.name!r} has no run mode in RUN_MODE — a person "
                "decides hitl/automate at migration (ADR-0034 D6)"
            )
        text += f"tags: [role, {RUN_MODE[comp.name]}]\n"
    rel = Path(*comp.name.split("::"))
    _write(out / comp.layer / rel.with_suffix(".yaml"), text, report)

    # Stripped, as the composer hands it to the prompt: the member's bytes are
    # what the agent reads today, and stage 2 compares them.
    injection = (comp.data.get("injection") or "").strip()
    if injection:
        _write(
            out / comp.layer / rel / "prompt.md",
            f"---\nschema_version: {SCHEMA_VERSION}\ntype: prompt\nblock: body\n---\n{injection}",
            report,
        )
    priorities = comp.data.get("priorities")
    if priorities:
        numbered = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(priorities))
        _write(
            out / comp.layer / rel / "priorities.md",
            f"---\nschema_version: {SCHEMA_VERSION}\ntype: prompt\nblock: PRIORITIES\n---\n{numbered}",
            report,
        )


def write_rule(out: Path, name: str, layer: str, rule_md: Path, report: Report) -> None:
    body = rule_md.read_text()
    _write(
        out / layer / "rules" / (name.replace("::", "/") + ".md"),
        f"---\nschema_version: {SCHEMA_VERSION}\ntype: prompt\nblock: RULES\nheading: {name}\n---\n{body}",
        report,
    )


def copy_skill_tree(src: Path, dst: Path, report: Report, library_root: Path) -> None:
    """The tree as is. A symlink that leaves the library root is dropped and named."""
    library = library_root.resolve()

    def ignore(folder: str, names: list[str]) -> set[str]:
        skipped = set(COPY_IGNORE(folder, names))
        for name in names:
            path = Path(folder) / name
            if not path.is_symlink():
                continue
            try:
                target = path.resolve()
            except OSError:
                target = None
            if target is None or not target.is_relative_to(library):
                skipped.add(name)
                report.note(
                    f"{path}: symlink to {path.readlink()} leaves the library root — not copied"
                )
        return skipped

    if dst.exists() or dst.is_symlink():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True, ignore=ignore)


def write_skill(
    out: Path, name: str, layer: str, folder: Path, report: Report, library_root: Path
) -> None:
    dst = out / layer / "skills" / name.replace("::", "/")
    copy_skill_tree(folder, dst, report, library_root)
    document = dst / "SKILL.md"
    document.write_text(rewrite_skill_frontmatter(document.read_text(), document, report))
    report.written.append(dst)


def rewrite_skill_frontmatter(raw: str, path: Path, report: Report) -> str:
    """Add ``schema_version`` / ``type`` under ``ai_hats:``, drop the deferred
    channels, touch nothing else — then prove it by parsing the result."""
    if not raw.startswith("---\n"):
        raise Refusal(f"{path}: SKILL.md has no frontmatter")
    lines = raw.splitlines(keepends=True)
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        raise Refusal(f"{path}: frontmatter never closes")
    front = lines[1:close]
    before = yaml.safe_load("".join(front)) or {}
    ours = before.get("ai_hats") or {}
    if not isinstance(ours, dict):
        raise Refusal(f"{path}: ai_hats is not a mapping")
    head = next((i for i, line in enumerate(front) if line.rstrip("\n") == "ai_hats:"), None)
    if head is None:
        if "ai_hats" in before:
            raise Refusal(f"{path}: ai_hats is written inline; only the block form is translated")
        front.append("ai_hats:\n  schema_version: 1\n  type: skill\n")
    else:
        end = head + 1
        while end < len(front) and (front[end].strip() == "" or front[end][0] in " \t"):
            end += 1
        block = front[head + 1 : end]
        indent = next((len(row) - len(row.lstrip()) for row in block if row.strip()), 2)
        kept: list[str] = []
        skipping = False
        for line in block:
            depth = len(line) - len(line.lstrip())
            if line.strip() and depth == indent:
                key = line.strip().split(":", 1)[0]
                skipping = key in DEFERRED_CHANNELS
            if not skipping:
                kept.append(line)
        pad = " " * indent
        front[head + 1 : end] = [f"{pad}schema_version: 1\n", f"{pad}type: skill\n", *kept]

    after = yaml.safe_load("".join(front)) or {}
    expected = dict(before)
    expected["ai_hats"] = {
        **{k: v for k, v in ours.items() if k not in DEFERRED_CHANNELS},
        "schema_version": SCHEMA_VERSION,
        "type": "skill",
    }
    if after != expected:
        raise Refusal(f"{path}: frontmatter rewrite did not parse back to the expected mapping")
    return "".join([lines[0], *front, *lines[close:]])


# ── hooks and operations ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class HookFile:
    layer: str
    rel: str
    full_name: str
    term: str
    text: str
    owners: frozenset[str]


def _frontmatter(path: Path) -> dict:
    raw = path.read_text()
    if not raw.startswith("---\n"):
        return {}
    lines = raw.splitlines(keepends=True)
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        raise Refusal(f"{path}: frontmatter never closes")
    data = yaml.safe_load("".join(lines[1:close])) or {}
    if not isinstance(data, dict):
        raise Refusal(f"{path}: frontmatter is not a mapping")
    return data


def _quote(value: str) -> str:
    if any(mark in value for mark in (":", ">", "|", "{", "}", "#")) or value.startswith("-"):
        return "'" + value.replace("'", "''") + "'"
    return value


def _point_stem(at: str) -> str:
    text = at.replace("->", "-")
    return text[1:] if text.startswith("-") else text


def _yaml_hook(kind: str, fields: list[tuple[str, str]]) -> str:
    lines = ["schema_version: 1", "type: hook", f"kind: {kind}"]
    lines.extend(f"{key}: {value}" for key, value in fields)
    return "\n".join(lines) + "\n"


def _hook(
    *,
    layer: str,
    rel: str,
    term: str,
    text: str,
    owners: frozenset[str],
) -> HookFile:
    full = "::".join(Path(rel).with_suffix("").parts)
    return HookFile(layer, rel, full, term, text, owners)


def _run_from(hook_rel: str, script: str) -> str:
    """``script`` is ``<skill>/<rest>`` with slashes; the hook file sits under ``hooks/``."""
    if "/" not in script:
        raise Refusal(f"{script!r} is not '<skill>/<path>'")
    up = "/".join([".."] * len(Path(hook_rel).parent.parts))
    return f"{up}/skills/{script}"


def _skill_hooks(layer: str, name: str, folder: Path, owners: frozenset[str]) -> list[HookFile]:
    ours = (_frontmatter(folder / "SKILL.md").get("ai_hats")) or {}
    if not isinstance(ours, dict):
        raise Refusal(f"{folder / 'SKILL.md'}: ai_hats is not a mapping")
    found: list[HookFile] = []
    git = ours.get("git_hooks") or {}
    if git:
        if not isinstance(git, dict):
            raise Refusal(f"{folder / 'SKILL.md'}: git_hooks is not a mapping")
        for event, scripts in git.items():
            if not isinstance(scripts, list) or not all(isinstance(s, str) for s in scripts):
                raise Refusal(f"{folder / 'SKILL.md'}: git_hooks.{event} is not a list of paths")
            for script in scripts:
                stem = Path(script).stem
                rel = f"hooks/{name.replace('::', '/')}/{stem}.yaml"
                found.append(
                    _hook(
                        layer=layer,
                        rel=rel,
                        term=stem,
                        owners=owners,
                        text=_yaml_hook(
                            "git",
                            [
                                ("at", _quote(str(event))),
                                ("run", _run_from(rel, f"{name.replace('::', '/')}/{script}")),
                            ],
                        ),
                    )
                )
    runtime = ours.get("runtime_hooks") or {}
    if runtime:
        if not isinstance(runtime, dict):
            raise Refusal(f"{folder / 'SKILL.md'}: runtime_hooks is not a mapping")
        for event, rows in runtime.items():
            if not isinstance(rows, list):
                raise Refusal(f"{folder / 'SKILL.md'}: runtime_hooks.{event} is not a list")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("script"), str):
                    raise Refusal(f"{folder / 'SKILL.md'}: a runtime hook needs script and matcher")
                script = row["script"]
                matcher = row.get("matcher", "")
                if not isinstance(matcher, str):
                    raise Refusal(f"{folder / 'SKILL.md'}: runtime matcher is not a string")
                stem = Path(script).stem
                rel = f"hooks/{name.replace('::', '/')}/{stem}.yaml"
                found.append(
                    _hook(
                        layer=layer,
                        rel=rel,
                        term=stem,
                        owners=owners,
                        text=_yaml_hook(
                            "runtime",
                            [
                                ("at", _quote(str(event))),
                                ("matcher", _quote(matcher)),
                                ("run", _run_from(rel, f"{name.replace('::', '/')}/{script}")),
                            ],
                        ),
                    )
                )
    worktree = ours.get("worktree") or {}
    if worktree:
        if not isinstance(worktree, dict):
            raise Refusal(f"{folder / 'SKILL.md'}: worktree is not a mapping")
        for at, rows in worktree.items():
            if at not in ("wt_in", "wt_out"):
                raise Refusal(f"{folder / 'SKILL.md'}: worktree.{at} is not wt_in or wt_out")
            if not isinstance(rows, list):
                raise Refusal(f"{folder / 'SKILL.md'}: worktree.{at} is not a list")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("script"), str):
                    raise Refusal(f"{folder / 'SKILL.md'}: a worktree hook needs a script")
                script = row["script"]
                stem = Path(script).stem
                rel = f"hooks/{name.replace('::', '/')}/{stem}.yaml"
                fields = [
                    ("at", at),
                    ("run", _run_from(rel, f"{name.replace('::', '/')}/{script}")),
                ]
                on = row.get("on")
                if on:
                    if not isinstance(on, list) or not all(isinstance(item, str) for item in on):
                        raise Refusal(f"{folder / 'SKILL.md'}: worktree on is not a list of events")
                    fields.append(("on", "[" + ", ".join(on) + "]"))
                found.append(
                    _hook(
                        layer=layer,
                        rel=rel,
                        term=stem,
                        owners=owners,
                        text=_yaml_hook("worktree", fields),
                    )
                )
    return found


def _apps_hooks(comp: Composite) -> list[HookFile]:
    apps = (comp.data.get("composition") or {}).get("apps") or {}
    if not apps:
        return []
    if not isinstance(apps, dict):
        raise Refusal(f"{comp.path}: composition.apps is not a mapping")
    found: list[HookFile] = []
    owners = frozenset({comp.name})

    def walk(node: object, path: tuple[str, ...]) -> None:
        label = f"{comp.path}: composition.apps.{'.'.join(path)}"
        if isinstance(node, list):
            for item in node:
                if not isinstance(item, dict):
                    raise Refusal(f"{label}: a row is not a mapping")
                emit_row(item, path, label)
            return
        if isinstance(node, dict):
            if "run" in node or "consent" in node:
                emit_row(node, path, label)
                return
            for key, child in node.items():
                walk(child, (*path, str(key)))
            return
        raise Refusal(f"{label}: expected a row or a mapping, got {type(node).__name__}")

    def emit_row(row: dict, path: tuple[str, ...], label: str) -> None:
        if not path:
            raise Refusal(f"{label}: a row has no app")
        app, *object_path = path
        at = row.get("at")
        points = [at] if isinstance(at, str) else at
        if not isinstance(points, list) or not points:
            raise Refusal(f"{label}: at must name a point")
        consent = row.get("consent")
        if app == "consent_gate":
            if consent is False:
                raise Refusal(
                    f"{label}: consent: false is a subtraction, and this slice does not write one"
                )
            if len(object_path) != 1:
                raise Refusal(f"{label}: a consent row needs exactly one operation")
            operation = object_path[0]
            for point in points:
                stem = _point_stem(str(point))
                rel = f"hooks/consent/{operation}/{stem}.yaml"
                found.append(
                    _hook(
                        layer=comp.layer,
                        rel=rel,
                        term=f"{operation}::{stem}",
                        owners=owners,
                        text=_yaml_hook(
                            "consent",
                            [("operation", operation), ("at", _quote(str(point)))],
                        ),
                    )
                )
            return
        run = row.get("run")
        if not isinstance(run, str) or "/" not in run:
            raise Refusal(f"{label}: run must be '<skill>/<path>'")
        on_error = row.get("on_error", "refuse")
        if on_error not in ("refuse", "warn"):
            raise Refusal(f"{label}: on_error {on_error!r} is not refuse or warn")
        skill = run.split("/", 1)[0]
        for point in points:
            # The script stem alone collides when one script is bound at two points.
            stem = f"{Path(run).stem}-{_point_stem(str(point))}"
            rel = f"hooks/{skill}/{stem}.yaml"
            fields = [("app", app), ("at", _quote(str(point))), ("run", _run_from(rel, run))]
            if object_path:
                fields.insert(1, ("object", ".".join(object_path)))
            fields.append(("on_error", str(on_error)))
            found.append(
                _hook(
                    layer=comp.layer,
                    rel=rel,
                    term=stem,
                    owners=owners,
                    text=_yaml_hook("workflow", fields),
                )
            )

    for app, block in apps.items():
        walk(block, (str(app),))
    return found


def collect_hooks(root: Path, picked: Closure) -> list[HookFile]:
    owners: dict[str, set[str]] = {}
    for comp in picked.composites.values():
        for skill in (comp.data.get("composition") or {}).get("skills") or []:
            owners.setdefault(skill, set()).add(comp.name)
    found: list[HookFile] = []
    seen: dict[tuple[str, str], str] = {}
    for name, (layer, folder) in picked.skills.items():
        found.extend(_skill_hooks(layer, name, folder, frozenset(owners.get(name, ()))))
    for comp in picked.composites.values():
        found.extend(_apps_hooks(comp))
    unique: list[HookFile] = []
    for hook in found:
        key = (hook.layer, hook.rel)
        previous = seen.get(key)
        if previous is not None and previous != hook.text:
            raise Refusal(
                f"{hook.layer}/{hook.rel}: two hook files want this path with different bodies"
            )
        if previous is None:
            seen[key] = hook.text
            unique.append(hook)
        else:
            # Same body from a second owner: the file is one, the term is named by both.
            held = unique[[(item.layer, item.rel) for item in unique].index(key)]
            unique[unique.index(held)] = HookFile(
                held.layer,
                held.rel,
                held.full_name,
                held.term,
                held.text,
                held.owners | hook.owners,
            )
    return unique


_OPERATIONS = (
    ("rack.transition", "surface: rack\nreader: rack_transition\nbinds_ticket: true\n"),
    ("wt.merge", "surface: ai-hats\nreader: wt_merge\nbinds_ticket: false\n"),
    ("wt.discard", "surface: ai-hats\nreader: wt_discard\nbinds_ticket: false\n"),
    (
        "fs.remove",
        "surface: none\nreader: fs_remove\nbinds_ticket: false\n"
        "hook_only_because: a shim under rm would sit under every script and the person's own shell\n",
    ),
)


def write_operations(out: Path, report: Report) -> None:
    for name, body in _OPERATIONS:
        text = f"schema_version: {SCHEMA_VERSION}\ntype: operation\n{body}"
        _write(out / "core" / "operations" / f"{name}.yaml", text, report)


def copy_shared_hooks(root: Path, out: Path, report: Report) -> None:
    src = root / "hooks"
    if not src.is_dir():
        return
    dst = out / "hooks"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True, ignore=COPY_IGNORE)
    report.written.append(dst)


# ── the run ──────────────────────────────────────────────────────────────────


def migrate(root: Path, only: Iterable[str]) -> Report:
    report = Report()
    out = root / EXPERIMENTAL
    picked = closure(root, read_composites(root), only)
    for comp in picked.composites.values():
        if comp.kind == "roles" and comp.name not in RUN_MODE:
            raise Refusal(
                f"{comp.path}: role {comp.name!r} has no run mode in RUN_MODE — a person "
                "decides hitl/automate at migration (ADR-0034 D6)"
            )
    hooks = collect_hooks(root, picked)
    names = flat_names(picked) | {hook.full_name for hook in hooks}
    by_owner: dict[str, list[HookFile]] = {}
    for hook in hooks:
        for owner in hook.owners:
            by_owner.setdefault(owner, []).append(hook)
    for comp in picked.composites.values():
        terms = [(hook.term, hook.full_name) for hook in by_owner.get(comp.name, ())]
        write_composite(out, comp, names, report, terms)
    for name, (layer, rule_md) in picked.rules.items():
        write_rule(out, name, layer, rule_md, report)
    for name, (layer, folder) in picked.skills.items():
        write_skill(out, name, layer, folder, report, root)
    for hook in hooks:
        _write(out / hook.layer / hook.rel, hook.text, report)
    write_operations(out, report)
    copy_shared_hooks(root, out, report)
    return report


def _terms(expr: str) -> list[tuple[int, str]]:
    sign = 1
    found: list[tuple[int, str]] = []
    for word in expr.split():
        if word == "+":
            sign = 1
            continue
        if word == "-":
            sign = -1
            continue
        found.append((sign, word))
        sign = 1
    return found


def carried_away(root: Path) -> list[str]:
    """Members a ``- <composite>`` removes that another added composite also brings."""
    composites: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(root.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict) or data.get("type") != "composite":
            continue
        name = "::".join(path.relative_to(root).with_suffix("").parts)
        composites[name] = _terms(str(data.get("expr") or ""))

    def brought(name: str) -> set[str]:
        """Base terms that survive opening ``name``, signs multiplied as in the fold."""
        present: list[str] = []

        def visit(current: str, sign: int, stack: tuple[str, ...]) -> None:
            if current in stack:
                return
            for term_sign, term in composites[current]:
                here = sign * term_sign
                if term in composites:
                    visit(term, here, (*stack, current))
                    continue
                if here > 0:
                    if term not in present:
                        present.append(term)
                elif term in present:
                    present.remove(term)

        visit(name, 1, ())
        return set(present)

    found: list[str] = []
    for terms in composites.values():
        added = [term for sign, term in terms if sign > 0 and term in composites]
        for sign, term in terms:
            if sign > 0 or term not in composites:
                continue
            mine = brought(term)
            others: set[str] = set()
            for other in added:
                if other == term:
                    continue
                others |= brought(other)
            for member in sorted(mine & others):
                if member not in found:
                    found.append(member)
    return found


def _put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text() == text:
        return
    path.write_text(text)


def _known_names(root: Path | None) -> set[str] | None:
    if root is None:
        return None
    names: set[str] = set()
    for path in root.rglob("*.yaml"):
        data = yaml.safe_load(path.read_text()) or {}
        if isinstance(data, dict) and data.get("type") == "composite":
            names.add(path.stem)
            names.add("::".join(path.relative_to(root).with_suffix("").parts))
    return names


def _stanza_expr(overlay: dict) -> str:
    parts: list[str] = []
    for bucket, sign in (("remove", "-"), ("add", "+")):
        block = overlay.get(bucket) or {}
        if not isinstance(block, dict):
            continue
        for key in ("traits", "rules", "skills"):
            for name in block.get(key) or []:
                parts.append(f"{sign} {name}")
    return " ".join(parts)


def _write_customizations(
    data: object, out: Path, notes: list[str], known: set[str] | None
) -> None:
    """The same override files for a user ``customizations.yaml`` and a project map."""
    if not isinstance(data, dict) or not data:
        notes.append("customization produced nothing")
        return
    for role, overlay in data.items():
        if known is not None and role not in known:
            notes.append(f"unknown role: {role}")
        overlay = overlay if isinstance(overlay, dict) else {}
        expr = _stanza_expr(overlay)
        prose = overlay.get("injection_append")
        if isinstance(prose, str) and prose.strip():
            term = f"customize-{role}-injection"
            body = prose if prose.endswith("\n") else f"{prose}\n"
            _put(
                out / f"{term}.md",
                f"---\nschema_version: {SCHEMA_VERSION}\ntype: prompt\nblock: body\n---\n{body}",
            )
            expr = f"{expr} + {term}".strip()
        if not expr:
            notes.append(f"customization produced nothing: {role}")
            continue
        _put(
            out / "overrides" / f"customize-{role}.yaml",
            f"schema_version: {SCHEMA_VERSION}\ntype: override\n"
            f'apply: "^{role}$"\nexpr: "{expr}"\n',
        )


def _rule_dirs(home: Path, project: dict | None) -> list[Path]:
    """``home/user-rules`` for ``~/.ai-hats``, plus the project's ``ai_hats_dir`` copy."""
    found: list[Path] = []
    direct = home / "user-rules"
    if direct.is_dir():
        found.append(direct)
    if project is None:
        return found
    ai_hats_dir = project.get("ai_hats_dir") or ".agent/ai-hats"
    if not isinstance(ai_hats_dir, str):
        ai_hats_dir = ".agent/ai-hats"
    laid = home / ai_hats_dir / "user-rules"
    if laid.is_dir() and all(laid.resolve() != path.resolve() for path in found):
        found.append(laid)
    return found


def migrate_home(home: Path, out: Path, known: set[str] | None) -> list[str]:
    """Turn today's customizations, user rules, paths and dropins into flat files."""
    notes: list[str] = []
    customs = home / "customizations.yaml"
    if customs.is_file():
        _write_customizations(_load_yaml(customs).get("customizations") or {}, out, notes, known)
    project_file = home / "ai-hats.yaml"
    project = _load_yaml(project_file) if project_file.is_file() else None
    if project is not None and "customizations" in project:
        _write_customizations(project.get("customizations") or {}, out, notes, known)
    rules: list[Path] = []
    for directory in _rule_dirs(home, project):
        found = sorted(directory.glob("*.md"))
        if not found:
            notes.append("user-rules produced nothing")
        rules.extend(found)
    rule_names: list[str] = []
    for rule in rules:
        rule_names.append(rule.stem)
        body = rule.read_text()
        if not body.endswith("\n"):
            body += "\n"
        _put(
            out / "rules" / f"{rule.stem}.md",
            f"---\nschema_version: {SCHEMA_VERSION}\ntype: prompt\nblock: RULES\n"
            f"heading: {rule.stem}\n---\n{body}",
        )
    if rule_names:
        expr = " ".join(f"+ {name}" for name in rule_names)
        _put(
            out / "overrides" / "user-rules.yaml",
            f'schema_version: {SCHEMA_VERSION}\ntype: override\napply: ".*"\nexpr: "{expr}"\n',
        )
    paths_file = home / "library_paths.yaml"
    if paths_file.is_file():
        paths = _load_yaml(paths_file).get("paths") or []
        lines = "\n".join(f"    - {item}" for item in paths)
        _put(out / "config.yaml", f"library:\n  paths:\n{lines}\n")
    if project is not None:
        if project.get("default_role"):
            _put(out / "ai-hats.yaml", f"library:\n  default: {project['default_role']}\n")
        if project.get("active_role"):
            _put(out / "local.yaml", f"active: {project['active_role']}\n")
    dropins: list[str] = []
    hooks = home / ".githooks"
    if hooks.is_dir():
        for event_dir in sorted(hooks.glob("*.d")):
            event = event_dir.name[: -len(".d")]
            for script in sorted(path for path in event_dir.iterdir() if path.is_file()):
                key = f"{event}-{script.stem}"
                filename = f"{key}{script.suffix}"
                dropins.append(key)
                dest = out / "hooks" / "dropin" / filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(script, dest)
                _put(
                    out / "hooks" / "dropin" / f"{key}.yaml",
                    f"schema_version: {SCHEMA_VERSION}\ntype: hook\nkind: git\n"
                    f"at: {event}\nrun: {filename}\n",
                )
    if dropins:
        expr = " ".join(f"+ {key}" for key in dropins)
        _put(
            out / "overrides" / "dropins.yaml",
            f'schema_version: {SCHEMA_VERSION}\ntype: override\napply: ".*"\nexpr: "{expr}"\n',
        )
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent
        / "packages"
        / "ai-hats-library"
        / "src"
        / "ai_hats_library",
        help="the builtin library root (default: this checkout's)",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        default=[],
        metavar="COMPOSITE",
        help="roles/traits whose closure to translate",
    )
    parser.add_argument("--home", type=Path, help="today's user/project dir to translate")
    parser.add_argument("--out", type=Path, help="where --home writes the flat files")
    parser.add_argument("--known", type=Path, help="flat root of composites that exist")
    parser.add_argument("--carried-away", type=Path, help="flat root to scan for - <composite>")
    args = parser.parse_args(argv)
    if args.carried_away is not None:
        for name in carried_away(args.carried_away):
            print(f"carried away: {name}")
        return 0
    if args.home is not None:
        if args.out is None:
            print("error: --home needs --out", file=sys.stderr)
            return 2
        for line in migrate_home(args.home, args.out, _known_names(args.known)):
            print(line)
        return 0
    if not all((args.root / layer).is_dir() for layer in LAYERS[:2]):
        print(f"error: {args.root} does not hold core/ and usage/", file=sys.stderr)
        return 2
    try:
        report = migrate(args.root, args.only)
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(report.written)} path(s) under {args.root / EXPERIMENTAL}")
    for line in report.deferred:
        print(f"deferred: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
