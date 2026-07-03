"""v0.6 -> v0.7 canonical-layout migration (HATS-408).

Pure core module — no click, no commit. The CLI wrapper in
``ai_hats.cli.maintenance`` owns flag parsing and the atomic git-commit envelope,
keeping the deletion / diff / refuse-vs-force logic unit-testable without Click.

Sweeps three shapes of stale role-content: Tier 1 (canonical role files under
``<ai_hats_dir>/``), Tier 2 (library mirror copies), Tier 3 (the same shape found
by globbing rather than trusting the legacy manifest, so a corrupt manifest can't
hide a stale file). For each finding we render the v0.6 baseline (from the live
``CompositionResult`` or the source library file); a whitespace-normalised diff
classifies it user-edited vs safe-to-delete. Orphans with no recoverable baseline
are treated as user-edited (``--force`` still deletes, default lists them).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .composer import CompositionResult, ResolvedComponent
from .frontmatter import FrontmatterError, read_frontmatter
from ai_hats_core import scrubbed_git_env
from .models import ProjectConfig
from .resolver import read_rule_body

logger = logging.getLogger(__name__)


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
# Tier-2 dir-mode parents — each child *directory* is a finding (rule/skill
# mirrors were written by v0.6 as whole subtrees: library/rules/<name>/rule.md
# + metadata.yaml, library/skills/<name>/SKILL.md, etc.).
_TIER2_PARENTS_DIR_MODE: dict[str, str] = {
    "library/rules": "lib_rule_dir",
    "library/skills": "lib_skill_dir",
}

# Tier-2 file-mode parents — each child *file* with one of the allowed
# suffixes is a finding (hooks were written by v0.6 as flat scripts:
# library/hooks/session_start.sh, library/hooks/session_end.py, etc., per
# HATS-314 commit 2eb329d which migrated ``.agent/hooks/`` flat layout to
# ``<ai_hats_dir>/library/hooks/`` keeping the flat shape via
# ``as_dir=False``). HATS-408 second-round review C1: the previous
# directory-only collector missed the v0.6 shape entirely AND would have
# swept undocumented user-owned ``library/hooks/<subdir>/`` content.
_TIER2_PARENTS_FILE_MODE: dict[str, tuple[str, tuple[str, ...]]] = {
    "library/hooks": ("lib_hook_file", (".sh", ".py")),
}
# Files inside Tier-2 mirror dirs that we treat as out-of-band (the marker
# itself, dotfiles) — they are deleted with the parent dir but never raise
# a user-edit flag because they are framework bookkeeping.
_TIER2_BOOKKEEPING_NAMES: frozenset[str] = frozenset({
    ".library_rules",         # v0.6 marker (pre-HATS-294) listing library rules
    ".ai-hats-managed",       # v0.6 marker (skills / hooks)
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
    """Return ``[(path, kind), ...]`` for every Tier-2 library mirror artefact.

    Two modes per parent:

    * **Dir mode** (rules/skills): each non-dot child *directory* is one
      finding (rule_dir, skill_dir).
    * **File mode** (hooks): each child *file* with an allowed suffix
      (``.sh`` / ``.py``) is one finding (hook_file) — matches v0.6
      ``as_dir=False`` flat-script layout.

    Dotfile markers (``.ai-hats-managed``, ``.library_rules``) at the
    parent level are NOT findings — they survive the sweep as harmless
    stale pointers; write_canonical owns canonical-side manifest hygiene.
    """
    if not canonical_dir.is_dir():
        return []
    out: list[tuple[Path, str]] = []
    for subpath, kind in _TIER2_PARENTS_DIR_MODE.items():
        parent = canonical_dir / subpath
        if not parent.is_dir():
            continue
        for entry in sorted(parent.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                out.append((entry, kind))
    for subpath, (kind, suffixes) in _TIER2_PARENTS_FILE_MODE.items():
        parent = canonical_dir / subpath
        if not parent.is_dir():
            continue
        for entry in sorted(parent.iterdir()):
            if (
                entry.is_file()
                and entry.suffix in suffixes
                and not entry.name.startswith(".")
            ):
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
    """Extract the frontmatter ``description`` from a skill's SKILL.md, or empty.

    Best-effort: a malformed block warns and falls back to ``""`` rather than
    aborting the migration diff.
    """
    try:
        data = read_frontmatter(skill.source_path / "SKILL.md")
    except FrontmatterError as exc:
        logger.warning(
            "skill %r: malformed SKILL.md frontmatter; omitting description from "
            "the migration baseline: %s",
            skill.name,
            exc,
        )
        return ""
    desc = data.get("description")
    return desc if isinstance(desc, str) else ""


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


def render_tier2_hook_file_baseline(
    hook_file: Path, hook_source_dirs: list[Path] | None
) -> str | None:
    """Reconstruct the v0.6 hook-file baseline by scanning library hook roots.

    v0.6 ``_collect_from_manifest(... as_dir=False)`` copied source library
    hook scripts verbatim to ``<ai_hats_dir>/library/hooks/<basename>``.
    For diff baseline we scan ``hook_source_dirs`` (the library_paths'
    hooks roots) for a matching filename and return its content.

    Returns None when no matching source is found → caller (planner)
    treats as user-edited (conservative — could be a user-authored
    script that v0.6 never wrote).
    """
    if not hook_source_dirs:
        return None
    for source_dir in hook_source_dirs:
        candidate = source_dir / hook_file.name
        if candidate.is_file():
            try:
                return candidate.read_text()
            except (OSError, UnicodeDecodeError):
                return None
    return None


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
    project_dir: Path | None = None,
    tier2_hook_source_dirs: list[Path] | None = None,
) -> MigrationReport:
    """Inspect ``canonical_dir`` and classify each finding.

    Pure: no writes, no git, no yaml mutation. Caller decides refuse vs
    force based on the resulting report.

    ``composition`` is the live compose(effective_role) result used to render
    Tier-1 baselines. ``tier2_source_lookup`` (optional) maps mirror-dir
    name → source root; absence means Tier-2 dirs all classify as user-edit
    (conservative).

    ``project_dir`` (optional) enables placeholder expansion on Tier-1
    baselines — v0.6 ``write_canonical`` called
    :func:`ai_hats.placeholders.expand_path_placeholders` on every
    rendered byte before write, so any baseline whose source content
    contains the literal ``<ai_hats_dir>`` token would diff-mismatch the
    expanded on-disk byte stream and falsely classify as a user edit
    (HATS-408 review A1). Tier-2 mirror bytes are NOT placeholder-expanded
    by v0.6 (they were verbatim file copies from the library source),
    so they skip this step.
    """
    report = MigrationReport()
    trait_baseline = composition.trait_injections
    # HATS-700: rule bodies are no longer eager-loaded into ``injection``; read
    # the deliverable text on demand from the resolved ``source_path`` (same
    # bytes the v0.6 canonical writer materialised) so edit-detection stays
    # byte-accurate.
    rule_baseline = {r.name: read_rule_body(r.source_path) for r in composition.rules}

    for path, kind in collect_tier1(canonical_dir):
        baseline = _tier1_baseline_for(
            kind=kind,
            name=path.stem,
            composition=composition,
            trait_baseline=trait_baseline,
            rule_baseline=rule_baseline,
        )
        if baseline is not None and project_dir is not None:
            from .placeholders import expand_path_placeholders
            baseline = expand_path_placeholders(baseline, project_dir)
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
        if kind == "lib_hook_file":
            baseline = render_tier2_hook_file_baseline(path, tier2_hook_source_dirs)
            if baseline is None:
                edited = True
                baseline_present = False
            else:
                actual_bytes = _safe_read(path)
                edited = is_user_edit(actual_bytes, baseline)
                baseline_present = True
        else:
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
            env=scrubbed_git_env(),
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
            env=scrubbed_git_env(),
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
                env=scrubbed_git_env(),
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


def execute_deletions(
    report: MigrationReport,
    canonical_dir: Path,
    *,
    project_dir: Path | None = None,
) -> list[Path]:
    """Move every finding to trash. Sweep empty parent dirs up to ``canonical_dir``.

    HATS-470: routed through :func:`ai_hats.safe_delete.discard` instead
    of raw ``unlink`` / ``rmtree``. Victims land under
    ``$TMPDIR/ai-hats/trash-<ts>-<pid>/<relpath>`` for recovery.

    Returns the list of paths actually removed (excludes already-missing
    targets so the caller can produce an accurate audit). Never touches
    ``user-rules/`` — defence in depth on top of the planner already not
    classifying it as a finding.

    HATS-408 review (B2 — symlink safety) — preserved via
    :func:`safe_delete.discard` semantics:

    * Uses lexical ``.absolute()`` for the user-rules guard so a malicious
      symlink at the finding (e.g. ``traits/foo.md`` → ``/etc/passwd``)
      cannot escape the check.
    * Symlinks: ``discard`` unlinks the link, target is preserved
      (sidecar ``.symlink`` file records the original target).
    * ``project_dir`` (when passed) controls trash project-relative
      layout — assembler caller passes ``self.project_dir``.
    """
    from .safe_delete import TrashFullError, discard

    removed: list[Path] = []
    canonical_absolute = canonical_dir.absolute()
    user_rules = canonical_absolute / "user-rules"
    for path in report.paths_to_delete:
        absolute = path.absolute()
        # Defence in depth: never delete anything lexically inside user-rules/.
        try:
            absolute.relative_to(user_rules)
            continue
        except ValueError:
            pass
        # ``exists()`` follows symlinks → returns False for broken links.
        # Check both so we don't silently skip a broken symlink finding.
        if not absolute.exists() and not absolute.is_symlink():
            continue
        # HATS-408 review B5: per-path try/except OSError surfaces one
        # stderr line per failure and the loop continues. HATS-470:
        # TrashFullError propagates (fatal — bump aborts).
        try:
            discard(absolute, reason="v07-migration", project_dir=project_dir)
        except TrashFullError:
            raise
        except OSError as e:
            sys.stderr.write(
                f"WARN: v07-migrate: could not remove {absolute} ({e.strerror or e}); "
                "fix permissions and re-run, or remove manually.\n"
            )
            continue
        removed.append(absolute)
        # Sweep empty parent dirs, but stop at canonical_dir itself.
        parent = absolute.parent
        while parent != canonical_absolute and canonical_absolute in parent.parents:
            try:
                next(parent.iterdir())
                break
            except (StopIteration, OSError):
                try:
                    parent.rmdir()  # safe-delete: ok empty-dir
                except OSError:
                    break
                parent = parent.parent
    return removed


# ---------- Public helpers (HATS-415: callable from Assembler) ----------


def migration_guidance(tier: int, kind: str, path_name: str) -> str:
    """Human-readable pointer to the new home for a v0.6 finding.

    Returns Rich-markup-friendly text — both the CLI (``console.print``)
    and ``AssemblyError`` consumers (caught by ``console.print``) render
    the markup. Plain stderr printers will see literal ``[bold]`` tags;
    that's acceptable for the user-edits refusal path because
    ``AssemblyError`` propagates to a Rich-aware catch in ``cli.main``.
    """
    if tier == 1:
        return (
            "Move project-wide content to [bold].agent/ai-hats/user-rules/<name>.md[/]; "
            "move role-specific content to "
            "[bold].agent/ai-hats/library/usage/{traits,rules,skills,roles}/...[/]."
        )
    if tier == 2:
        bucket = {
            "lib_rule_dir": "rules",
            "lib_skill_dir": "skills",
            "lib_hook_file": "hooks",
        }.get(kind, "rules")
        if kind == "lib_hook_file":
            return (
                f"v0.6 library-hook leftover. Move custom logic to "
                f"[bold].agent/ai-hats/library/usage/hooks/{path_name}[/] "
                "or delete after confirming."
            )
        return (
            f"Move overrides to [bold].agent/ai-hats/library/usage/{bucket}/{path_name}/...[/]."
        )
    return ""


def empty_composition() -> CompositionResult:
    """Return an empty CompositionResult for projects without an effective role.

    Used when both ``active_role`` and ``default_role`` are unset — no role to
    compose, so any Tier-1 file on disk is by definition baselineless and
    classifies as a user edit (conservative — release-gate fork A).
    """
    return CompositionResult(
        name="",
        priorities=[],
        rules=[],
        skills=[],
        injections=[],
    )


def render_user_edits_refusal(
    user_edits: list[TierFinding], project_dir: Path
) -> str:
    """Render the ``AssemblyError`` message body for a user-edits refusal.

    Used by ``Assembler._run_v07_migration`` to surface the same per-file
    guidance that the old ``self migrate-v07`` CLI command printed —
    relocated here so the assembler stays library-pure (no Rich import).
    The returned string carries Rich markup; the CLI-side AssemblyError
    catcher passes it through ``console.print`` which renders it.
    """
    lines: list[str] = [
        "v0.6 canonical layout detected — user edits found on disk:",
        "",
    ]
    for f in user_edits:
        try:
            rel = f.path.relative_to(project_dir)
        except ValueError:
            rel = f.path
        lines.append(f"  [yellow]{rel}[/]")
        lines.append(
            f"    → tier {f.tier} ({f.kind}). "
            + migration_guidance(f.tier, f.kind, f.path.name)
        )
        lines.append("")
    lines.append(
        "Re-run with [bold]--migrate-force[/] to overwrite (logs WARN per file) "
        "after relocating the content."
    )
    return "\n".join(lines)
