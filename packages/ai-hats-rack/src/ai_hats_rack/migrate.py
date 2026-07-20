"""One-shot flat → dir-per-card migration for the HYP/PROP backlogs (HATS-1044,
ADR-0017 §5/R5) — rack-side API + a ``python -m ai_hats_rack.migrate`` entry.

Moves each ``<catalog>/<ID>[-slug].yaml`` to ``<catalog>/<ID>/task.yaml`` and seeds
``backlog.yaml`` from the packaged definition. Mapping is a pure rename
(``status``→``state``; link kinds→the ``links`` map, scalars→one-element lists;
every other key verbatim). Dry-run + inventory diff (counts, id sets, a per-card
round-trip proving nothing is lost); idempotent (an existing ``<ID>/task.yaml`` is
skipped); the flat source stays unless ``purge_source`` (the supervisor's
live-gate call). A migrated card's zero-events journal is the documented K7
expectation.
"""  # comment-length: allow

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .definition import load_packaged_definition, packaged_definition_source
from .models import TaskCard, atomic_write_text

#: The two shipped non-default backlogs and where their flat files live under a
#: ``<ai_hats_dir>/tracker`` root (same dir the new ``<ID>/`` dirs land in).
_CATALOGS = (
    ("hypotheses", Path("hypotheses"), re.compile(r"^(HYP-\d+).*\.ya?ml$")),
    ("proposals", Path("backlog") / "proposals", re.compile(r"^(PROP-\d+)\.ya?ml$")),
)


@dataclass(frozen=True)
class CardMigration:
    """One flat file's outcome: ``migrated`` (written), ``skipped`` (dest already
    present — idempotent re-run), or ``mismatch`` (round-trip lost data — a
    fail-closed refusal to write)."""

    card_id: str
    source: Path
    dest: Path
    outcome: str  # migrated | skipped | mismatch
    detail: str = ""


@dataclass
class CatalogReport:
    name: str
    catalog: Path
    backlog_written: bool
    cards: list[CardMigration] = field(default_factory=list)

    @property
    def source_ids(self) -> set[str]:
        return {c.card_id for c in self.cards}

    @property
    def migrated_ids(self) -> set[str]:
        return {c.card_id for c in self.cards if c.outcome == "migrated"}

    @property
    def mismatches(self) -> list[CardMigration]:
        return [c for c in self.cards if c.outcome == "mismatch"]


@dataclass
class MigrationReport:
    catalogs: list[CatalogReport] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        """No card lost data on the round-trip — the inventory diff is clean."""
        return all(not c.mismatches for c in self.catalogs)

    def render(self) -> str:
        lines: list[str] = [f"migration {'(dry-run) ' if self.dry_run else ''}report"]
        for cat in self.catalogs:
            lines.append(
                f"  {cat.name} @ {cat.catalog}: {len(cat.source_ids)} flat card(s); "
                f"{len(cat.migrated_ids)} migrated, "
                f"{sum(1 for c in cat.cards if c.outcome == 'skipped')} skipped, "
                f"{len(cat.mismatches)} mismatch; "
                f"backlog.yaml {'written' if cat.backlog_written else 'unchanged'}"
            )
            if cat.source_ids:
                lines.append(f"    ids: {', '.join(sorted(cat.source_ids))}")
            for c in cat.mismatches:
                lines.append(f"    MISMATCH {c.card_id}: {c.detail}")
        lines.append(f"  status: {'OK' if self.ok else 'MISMATCHES — nothing lost check FAILED'}")
        return "\n".join(lines)


# ----- field mapping (pure rename; nothing dropped) ---------------------------


def _link_names(definition_name: str) -> dict[str, str]:
    """``{kind name: arity}`` for the non-derived link kinds of a packaged
    definition — the top-level keys migrated into the ``links`` map."""
    defn = load_packaged_definition(definition_name)
    return {k.name: k.arity for k in defn.links_registry.kinds if not k.derived}


def _map_card(raw: dict, link_names: dict[str, str]) -> dict:
    """Flat tracker dict → rack task.yaml dict: ``status`` → ``state``, link kinds
    into the ``links`` map (scalars → single-element lists), the rest verbatim."""
    out: dict = {"id": raw["id"], "title": raw.get("title", ""), "state": raw.get("status", "")}
    links: dict[str, list] = {}
    for key, value in raw.items():
        if key in ("id", "title", "status"):
            continue
        if key in link_names:
            ids = [v for v in (value if isinstance(value, list) else [value]) if v]
            if ids:
                links[key] = ids
            continue
        out[key] = value
    if links:
        out["links"] = links
    return out


def _unmap_card(mapped: dict, link_names: dict[str, str]) -> dict:
    """Rack task.yaml dict → flat tracker dict — the inverse used ONLY by the
    round-trip check (the tracker io shim reimplements it; rack cannot import it)."""
    out: dict = {"id": mapped["id"], "title": mapped["title"], "status": mapped["state"]}
    for key, value in mapped.items():
        if key in ("id", "title", "state", "links"):
            continue
        out[key] = value
    for name, ids in (mapped.get("links") or {}).items():
        out[name] = ids[0] if link_names.get(name) == "one" else list(ids)
    return out


def _normalize(d: dict) -> dict:
    """Drop empty values so an empty link list (``related_hypotheses: []``) reads
    as equivalent to its absence after the migrate/unmigrate round-trip."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _roundtrip_ok(raw: dict, mapped: dict, link_names: dict[str, str]) -> tuple[bool, str]:
    """The card survived migration losslessly: it reloads as a rack card AND
    unmapping it reproduces the source (empties normalized away)."""
    try:
        TaskCard.model_validate(mapped)
    except Exception as exc:  # noqa: BLE001 — surface the exact load failure in the report
        return False, f"not loadable as a rack card: {exc}"
    back = _normalize(_unmap_card(mapped, link_names))
    want = _normalize(raw)
    if back != want:
        missing = {k: want[k] for k in want if want.get(k) != back.get(k)}
        return False, f"round-trip differs on {sorted(missing)}"
    return True, ""


# ----- migration --------------------------------------------------------------


def _flat_sources(catalog: Path, pattern: re.Pattern) -> list[tuple[str, Path]]:
    """``(id, path)`` for each flat ``<ID>[-slug].yaml`` file in the catalog dir,
    excluding ``backlog.yaml`` and the new ``<ID>/`` dirs (glob is one level)."""
    if not catalog.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for p in sorted(catalog.glob("*.yaml")):
        m = pattern.match(p.name)
        if m:
            out.append((m.group(1), p))
    return out


def _seed_backlog(catalog: Path, definition_name: str, *, dry_run: bool) -> bool:
    """Write ``<catalog>/backlog.yaml`` from the packaged definition; idempotent
    (unchanged when already identical). Returns whether a write happened."""
    text = packaged_definition_source(definition_name)
    dest = catalog / "backlog.yaml"
    if dest.is_file() and dest.read_text(encoding="utf-8") == text:
        return False
    if not dry_run:
        catalog.mkdir(parents=True, exist_ok=True)
        atomic_write_text(dest, text)
    return True


def migrate_catalog(
    catalog: Path, definition_name: str, *, dry_run: bool = False, purge_source: bool = False
) -> CatalogReport:
    """Migrate one flat catalog dir to dir-per-card + seed its ``backlog.yaml``.

    Idempotent: an existing ``<ID>/task.yaml`` is skipped (never overwritten). A
    card that fails the round-trip is reported as a ``mismatch`` and NOT written —
    the source flat file is always left in place (removed only with
    ``purge_source``, after a clean round-trip)."""
    _, subpath, pattern = next(c for c in _CATALOGS if c[0] == definition_name)
    link_names = _link_names(definition_name)
    report = CatalogReport(
        name=definition_name,
        catalog=catalog,
        backlog_written=_seed_backlog(catalog, definition_name, dry_run=dry_run),
    )
    for card_id, source in _flat_sources(catalog, pattern):
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        dest = catalog / card_id / "task.yaml"
        mapped = _map_card(raw, link_names)
        ok, detail = _roundtrip_ok(raw, mapped, link_names)
        if not ok:
            report.cards.append(CardMigration(card_id, source, dest, "mismatch", detail))
            continue
        if dest.exists():
            report.cards.append(CardMigration(card_id, source, dest, "skipped", "dest exists"))
            continue
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                dest, yaml.safe_dump(mapped, sort_keys=False, allow_unicode=True)
            )
            if purge_source:
                source.unlink()  # safe-delete: ok migrated-flat-source (gated, clean round-trip)
        report.cards.append(CardMigration(card_id, source, dest, "migrated"))
    return report


def migrate_tracker(
    ai_hats_dir: Path, *, dry_run: bool = False, purge_source: bool = False
) -> MigrationReport:
    """Migrate BOTH catalogs under ``<ai_hats_dir>/tracker`` (hypotheses +
    proposals). ``ai_hats_dir`` is the dir that holds ``tracker/`` (e.g.
    ``<project>/.agent/ai-hats``)."""
    tracker = ai_hats_dir / "tracker"
    report = MigrationReport(dry_run=dry_run)
    for name, subpath, _pattern in _CATALOGS:
        report.catalogs.append(
            migrate_catalog(
                tracker / subpath, name, dry_run=dry_run, purge_source=purge_source
            )
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ai_hats_rack.migrate",
        description="One-shot flat → dir-per-card migration for the HYP/PROP backlogs.",
    )
    parser.add_argument("ai_hats_dir", type=Path, help="Dir holding tracker/ (e.g. .agent/ai-hats)")
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    parser.add_argument(
        "--purge-source",
        action="store_true",
        help="Delete each flat file after a clean migration (supervisor-gated live option)",
    )
    args = parser.parse_args(argv)
    report = migrate_tracker(
        args.ai_hats_dir, dry_run=args.dry_run, purge_source=args.purge_source
    )
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
