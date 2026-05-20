"""HATS-408: v0.6 → v0.7 canonical-layout migration.

Pure core module — no click, no subprocess for commit. The CLI wrapper in
:mod:`ai_hats.cli.maintenance` is responsible for flag parsing and the
atomic git commit envelope. Splitting concerns this way keeps the
deletion logic / diff engine / refuse-vs-force decision tree fully
unit-testable without Click overhead.

Migration shape (per the HATS-408 plan):

* **Tier 1** — canonical role-content under ``<ai_hats_dir>/``: ``priorities.md``,
  ``role.md``, ``traits/*.md``, ``rules/*.md``, ``skills_index.md``.
* **Tier 2** — library mirror copies under ``<ai_hats_dir>/library/rules/<name>/``
  and ``<ai_hats_dir>/library/skills/<name>/``.
* **Tier 3 (manifest-blind)** — same Tier-1/2 *shape* found by globbing rather
  than trusting the legacy ``MANAGED`` / ``.ai-hats-managed`` markers; folded
  into the Tier-1/2 collectors so a corrupt/missing manifest can never hide a
  stale file from the sweep.

For every finding we attempt to render the v0.6 baseline from the live
``CompositionResult`` (Tier 1) or from the source library file (Tier 2). A
whitespace-normalised diff between baseline and on-disk content classifies
the file as user-edited or safe-to-delete. Files with no recoverable baseline
(true manifest-blind orphans) are always treated as user-edited — under
``--force`` they still get deleted, but the default refuse path lists them so
the user can review.

The v0.6 renderers (``_render_priorities``, ``_render_role``,
``_render_skills_index``) were deleted by HATS-294 commit ``f124d16``. We
reconstruct only the bytes-stable subset needed for diffing here; full
re-materialisation is intentionally out of scope.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .composer import CompositionResult, ResolvedComponent
from .models import ProjectConfig


# Tier-1 fixed root files and the kind tag each carries.
_TIER1_ROOT_FILES: dict[str, str] = {
    "priorities.md": "priorities",
    "role.md": "role",
    "skills_index.md": "skill_index",
}
# Tier-1 directories whose ``*.md`` children are individual findings.
_TIER1_SUBDIRS: dict[str, str] = {
    "traits": "trait",
    "rules": "rule",
}
# Tier-2 mirror parents under canonical/. Each child *directory* is a finding.
_TIER2_PARENTS: dict[str, str] = {
    "library/rules": "lib_rule_dir",
    "library/skills": "lib_skill_dir",
}
# Files inside Tier-2 mirror dirs that we treat as out-of-band (the marker
# itself, dotfiles) — they are deleted with the parent dir but never raise
# a user-edit flag because they are framework bookkeeping.
_TIER2_BOOKKEEPING_NAMES: frozenset[str] = frozenset({
    ".library_rules",         # v0.6 marker (pre-HATS-294) listing library rules
    ".ai-hats-managed",       # v0.6 marker (skills / hooks) — MANAGED_SKILLS_MARKER
})


# ---------- Dataclasses ----------


@dataclass(frozen=True)
class TierFinding:
    """One on-disk artefact considered by the migration.

    ``path`` is absolute (caller code joins on it). ``tier`` is 1 for canonical
    role-content, 2 for library mirror directories. ``kind`` is a stable tag
    used by the refusal renderer to point users at the right new home
    (``user-rules/`` vs ``library/usage/...``).
    """

    path: Path
    tier: int
    kind: str
    is_user_edit: bool
    baseline_present: bool


@dataclass
class MigrationReport:
    """What ``plan_migration`` discovered. Pure data — no IO.

    ``yaml_changes`` is a list of human-readable strings describing what
    ``ProjectConfig.from_yaml`` healed during load (deprecated-field strip,
    default_role heal). They are computed by peeking at the raw yaml dict
    *before* model_validate so we can report them even when nothing on the
    filesystem needs touching.
    """

    findings: list[TierFinding] = field(default_factory=list)
    yaml_changes: list[str] = field(default_factory=list)

    @property
    def user_edits(self) -> list[TierFinding]:
        """Findings that diff beyond whitespace vs the v0.6 baseline."""
        return [f for f in self.findings if f.is_user_edit]

    @property
    def safe_deletions(self) -> list[TierFinding]:
        """Findings where on-disk content matches baseline → safe to remove."""
        return [f for f in self.findings if not f.is_user_edit]

    @property
    def paths_to_delete(self) -> list[Path]:
        """All sweep targets (user-edit + safe) sorted for deterministic output."""
        return sorted({f.path for f in self.findings})

    @property
    def has_work(self) -> bool:
        """True iff anything needs changing on disk or in yaml."""
        return bool(self.findings) or bool(self.yaml_changes)


# ---------- Collectors ----------


def collect_tier1(canonical_dir: Path) -> list[tuple[Path, str]]:
    """Return ``[(file_path, kind), ...]`` for every Tier-1 on-disk file.

    Globs by shape — does *not* consult ``MANAGED``. A corrupt or missing
    manifest cannot hide a stale file from the sweep.
    """
    if not canonical_dir.is_dir():
        return []
    out: list[tuple[Path, str]] = []
    for name, kind in _TIER1_ROOT_FILES.items():
        p = canonical_dir / name
        if p.is_file():
            out.append((p, kind))
    for subdir, kind in _TIER1_SUBDIRS.items():
        d = canonical_dir / subdir
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            out.append((md, kind))
    return out


def collect_tier2(canonical_dir: Path) -> list[tuple[Path, str]]:
    """Return ``[(mirror_dir_path, kind), ...]`` for every Tier-2 library mirror dir.

    Each direct child *directory* under ``library/rules/`` and
    ``library/skills/`` is one finding (dotfiles / marker files are ignored —
    they go away with the parent dir during sweep).
    """
    if not canonical_dir.is_dir():
        return []
    out: list[tuple[Path, str]] = []
    for subpath, kind in _TIER2_PARENTS.items():
        parent = canonical_dir / subpath
        if not parent.is_dir():
            continue
        for entry in sorted(parent.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                out.append((entry, kind))
    return out


# ---------- Baseline rendering (v0.6 shape from compose) ----------


def render_priorities_md(priorities: list[str]) -> str | None:
    """Reconstruct the v0.6 ``priorities.md`` content. None if priorities empty."""
    if not priorities:
        return None
    body = "\n".join(f"{i}. {p}" for i, p in enumerate(priorities, start=1))
    return f"# Priorities\n\n{body}\n"


def render_role_md(role_text: str, overlay_text: str) -> str | None:
    """Reconstruct the v0.6 ``role.md`` content. None if both parts empty."""
    parts = [t for t in (role_text, overlay_text) if t]
    if not parts:
        return None
    return _ensure_trailing_newline("\n\n".join(parts))


def render_trait_md(text: str) -> str | None:
    """Reconstruct the v0.6 ``traits/<name>.md`` content."""
    if not text:
        return None
    return _ensure_trailing_newline(text)


def render_rule_md(text: str) -> str | None:
    """Reconstruct the v0.6 ``rules/<name>.md`` content."""
    if not text:
        return None
    return _ensure_trailing_newline(text)


def render_skills_index_md(skills: list[ResolvedComponent]) -> str | None:
    """Reconstruct the v0.6 ``skills_index.md`` (bytes-stable subset).

    We render only the top one-liner-per-skill section. The Routing / Skip
    tables embed metadata that depends on resolver state; reconstructing them
    perfectly is brittle and not worth the surface area for a diff baseline.

    Effect on diffs: a v0.6 ``skills_index.md`` that *contained* Routing / Skip
    tables will diff vs this baseline and be flagged as a user edit — but the
    refuse path is recoverable (run with ``--force`` to delete; the v0.7
    composer regenerates the routing tables in-memory per session). The
    trade-off favours simplicity over false-negative coverage.
    """
    if not skills:
        return None
    lines = ["# Skills Index", ""]
    for skill in skills:
        desc = _skill_description(skill)
        if desc == skill.name or not desc:
            lines.append(f"- **{skill.name}**")
        else:
            lines.append(f"- **{skill.name}** — {desc}")
    return "\n".join(lines) + "\n"


def _skill_description(skill: ResolvedComponent) -> str:
    """Extract the frontmatter ``description`` from a skill's SKILL.md, or empty."""
    skill_md = skill.source_path / "SKILL.md"
    if not skill_md.is_file():
        return ""
    try:
        text = skill_md.read_text()
    except OSError:
        return ""
    # Minimal frontmatter parse: between leading ``---`` lines, look for
    # ``description: ...`` (single-line value). Mirrors the v0.6 behaviour
    # of providers._extract_frontmatter_description without taking a
    # cross-module dependency on a private symbol.
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    if end == -1:
        return ""
    for line in text[4:end].splitlines():
        line = line.strip()
        if line.startswith("description:"):
            return line[len("description:"):].strip().strip('"').strip("'")
    return ""


def _ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


# ---------- Diff engine ----------


def is_user_edit(actual: bytes, baseline: str | None) -> bool:
    """True iff ``actual`` differs from ``baseline`` beyond whitespace.

    Normalisation strips trailing whitespace per line, collapses runs of blank
    lines to one, and strips leading/trailing whitespace on the whole file.
    Editor save-on-save churn (final newline, blank-line tidying) thus never
    triggers a false-positive user edit.

    A ``None`` baseline means we cannot reconstruct the v0.6 shape (manifest-
    blind orphan, or a component the current compose path no longer
    resolves). Per the HATS-408 plan §0 fork A, these are treated as user
    edits — conservative default for a release gate that destroys files.
    """
    if baseline is None:
        return True
    try:
        actual_text = actual.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return _normalize(actual_text) != _normalize(baseline)


def _normalize(text: str) -> str:
    """Whitespace-normalisation used by ``is_user_edit``. Module-private."""
    lines = [line.rstrip() for line in text.splitlines()]
    collapsed: list[str] = []
    prev_blank = False
    for line in lines:
        if not line:
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        collapsed.append(line)
    return "\n".join(collapsed).strip()


# ---------- Tier-2 baseline lookup ----------


def render_tier2_dir_baseline(
    mirror_dir: Path, source_lookup: dict[str, Path] | None
) -> str | None:
    """Compute a baseline hash-equivalent string for a whole library mirror dir.

    Strategy: for each file under ``mirror_dir``, look up the corresponding
    source file (by relative path) under ``source_lookup[<component_name>]``
    and concatenate their contents in sorted-relpath order. If any file
    cannot be resolved to a source, returns None → treated as user edit.

    ``source_lookup`` maps component-name → its source root in the library
    layer that resolved it. Empty / None means "no library available" → all
    Tier-2 dirs become baselineless (user-edit, conservative).
    """
    if not source_lookup:
        return None
    name = mirror_dir.name
    source_root = source_lookup.get(name)
    if source_root is None or not source_root.is_dir():
        return None
    actual_files = sorted(
        p for p in mirror_dir.rglob("*")
        if p.is_file() and p.name not in _TIER2_BOOKKEEPING_NAMES
    )
    if not actual_files:
        return ""  # empty dir matches empty baseline → safe to delete
    parts: list[str] = []
    for f in actual_files:
        rel = f.relative_to(mirror_dir)
        src = source_root / rel
        if not src.is_file():
            return None
        try:
            parts.append(f"{rel}\n{src.read_text()}")
        except OSError:
            return None
    return "\n----\n".join(parts)


def render_tier2_dir_actual(mirror_dir: Path) -> str:
    """Mirror of ``render_tier2_dir_baseline`` for the on-disk side."""
    actual_files = sorted(
        p for p in mirror_dir.rglob("*")
        if p.is_file() and p.name not in _TIER2_BOOKKEEPING_NAMES
    )
    if not actual_files:
        return ""
    parts: list[str] = []
    for f in actual_files:
        rel = f.relative_to(mirror_dir)
        try:
            parts.append(f"{rel}\n{f.read_text()}")
        except (OSError, UnicodeDecodeError):
            # Binary or unreadable → return a sentinel that won't match any
            # baseline, forcing user-edit classification.
            return "<<unreadable>>"
    return "\n----\n".join(parts)


# ---------- Planner ----------


def plan_migration(
    canonical_dir: Path,
    composition: CompositionResult,
    tier2_source_lookup: dict[str, Path] | None = None,
) -> MigrationReport:
    """Inspect ``canonical_dir`` and classify each finding.

    Pure: no writes, no git, no yaml mutation. Caller decides refuse vs
    force based on the resulting report.

    ``composition`` is the live compose(effective_role) result used to render
    Tier-1 baselines. ``tier2_source_lookup`` (optional) maps mirror-dir
    name → source root; absence means Tier-2 dirs all classify as user-edit
    (conservative).
    """
    report = MigrationReport()
    trait_baseline = composition.trait_injections
    rule_baseline = {r.name: r.injection for r in composition.rules}

    for path, kind in collect_tier1(canonical_dir):
        baseline = _tier1_baseline_for(
            kind=kind,
            name=path.stem,
            composition=composition,
            trait_baseline=trait_baseline,
            rule_baseline=rule_baseline,
        )
        actual = _safe_read(path)
        edited = is_user_edit(actual, baseline)
        report.findings.append(
            TierFinding(
                path=path,
                tier=1,
                kind=kind,
                is_user_edit=edited,
                baseline_present=baseline is not None,
            )
        )

    for path, kind in collect_tier2(canonical_dir):
        baseline = render_tier2_dir_baseline(path, tier2_source_lookup)
        if baseline is None:
            edited = True
            baseline_present = False
        else:
            actual = render_tier2_dir_actual(path)
            edited = _normalize(actual) != _normalize(baseline)
            baseline_present = True
        report.findings.append(
            TierFinding(
                path=path,
                tier=2,
                kind=kind,
                is_user_edit=edited,
                baseline_present=baseline_present,
            )
        )

    return report


def _tier1_baseline_for(
    *,
    kind: str,
    name: str,
    composition: CompositionResult,
    trait_baseline: dict[str, str],
    rule_baseline: dict[str, str],
) -> str | None:
    """Dispatch baseline rendering by kind. None when no baseline recoverable."""
    if kind == "priorities":
        return render_priorities_md(composition.priorities)
    if kind == "role":
        return render_role_md(composition.role_injection, composition.overlay_injection)
    if kind == "skill_index":
        return render_skills_index_md(composition.skills)
    if kind == "trait":
        return render_trait_md(trait_baseline.get(name, ""))
    if kind == "rule":
        return render_rule_md(rule_baseline.get(name, ""))
    return None


def _safe_read(path: Path) -> bytes:
    """Read ``path`` as bytes; on any error return a sentinel that mismatches."""
    try:
        return path.read_bytes()
    except OSError:
        return b"<<unreadable>>"


# ---------- Yaml change detection ----------

# Mirrors models._DEPRECATED_PROJECT_FIELDS but referenced directly so a
# rename on the models side surfaces here too.
def detect_yaml_changes(raw_yaml: dict, config: ProjectConfig) -> list[str]:
    """Compare the raw on-disk yaml dict to the post-load config and return
    a list of human-readable change descriptions.

    ``ProjectConfig.from_yaml`` already strips/heals in memory; this function
    only describes what *would change on disk* if we re-saved.
    """
    from .models import _DEPRECATED_PROJECT_FIELDS

    changes: list[str] = []
    for field_name in sorted(_DEPRECATED_PROJECT_FIELDS):
        if field_name in raw_yaml:
            changes.append(f"strip deprecated field {field_name!r}")
    raw_default = raw_yaml.get("default_role") or ""
    if config.active_role and not raw_default:
        changes.append(f"heal default_role := {config.active_role!r}")
    return changes


# ---------- Branch scanner ----------


def check_branches_modify_paths(
    project_dir: Path, paths: list[Path], timeout: float = 10.0
) -> list[tuple[str, list[str]]]:
    """Return ``[(branch, [relpath, ...])]`` for local branches that touch any of ``paths``.

    Local-only. Errors (no git, timeout, detached HEAD) collapse to an empty
    list — best-effort warning, never a blocker.
    """
    if not paths:
        return []
    try:
        result = subprocess.run(
            [
                "git", "-C", str(project_dir),
                "for-each-ref", "--format=%(refname:short)", "refs/heads/",
            ],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if result.returncode != 0:
        return []
    branches = [b.strip() for b in result.stdout.splitlines() if b.strip()]

    try:
        head = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        current = head.stdout.strip() if head.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        current = ""

    rel_paths: list[str] = []
    for p in paths:
        try:
            rel_paths.append(str(p.relative_to(project_dir)))
        except ValueError:
            continue
    if not rel_paths:
        return []

    findings: list[tuple[str, list[str]]] = []
    for branch in branches:
        if branch == current or branch == "HEAD":
            continue
        try:
            diff = subprocess.run(
                ["git", "-C", str(project_dir), "diff", "--name-only",
                 f"HEAD..{branch}", "--", *rel_paths],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            continue
        if diff.returncode != 0:
            continue
        modified = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
        if modified:
            findings.append((branch, sorted(modified)))
    return findings


# ---------- Execution ----------


def execute_deletions(report: MigrationReport, canonical_dir: Path) -> list[Path]:
    """Delete every path in the report. Sweep empty parent dirs up to ``canonical_dir``.

    Returns the list of paths actually removed (excludes already-missing
    targets so the caller can produce an accurate audit). Never touches
    ``user-rules/`` — defence in depth on top of the planner already not
    classifying it as a finding.
    """
    removed: list[Path] = []
    canonical_resolved = canonical_dir.resolve()
    user_rules = canonical_resolved / "user-rules"
    for path in report.paths_to_delete:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        # Defence in depth: never delete anything inside user-rules/.
        try:
            resolved.relative_to(user_rules)
            continue
        except ValueError:
            pass
        if not resolved.exists():
            continue
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink(missing_ok=True)
        removed.append(resolved)
        # Sweep empty parent dirs, but stop at canonical_dir itself.
        parent = resolved.parent
        while parent != canonical_resolved and canonical_resolved in parent.parents:
            try:
                next(parent.iterdir())
                break
            except (StopIteration, OSError):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    return removed
