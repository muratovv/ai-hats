"""Assembling a surface's system prompt, and writing it into a managed block.

Beside the contract rather than inside it (ADR-0026 D14, HATS-1826). Reading a rule's
``metadata.yaml``, a skill's frontmatter and a marker-delimited file is knowledge of
this application's layout, and a contract carrying it makes every implementor inherit
ai-hats internals to declare itself. The seams stay on ``Surface``: what left the
contract is the knowledge, not the extension point.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ai_hats_core import CompositionResult, ResolvedComponent
from ai_hats_core.safe_delete import replace as _safe_replace

from ..constants import (
    INJECTION_END,
    INJECTION_START,
    PUBLISH_AGGREGATOR_END,
    PUBLISH_AGGREGATOR_START,
)
from ..models import RuleMetadata
from ..resolver import read_rule_body

logger = logging.getLogger(__name__)


def compose_sections(result: CompositionResult) -> str:
    """Assemble the shared system-prompt sections.

    Order: PRIORITIES → merged role/trait injection → always-on RULES → USER RULES.

    No skill index: every surface has a native skill registry that already carries
    each description, so the text index was a duplicate. It was a per-surface toggle
    (HATS-701) whose last ``True`` caller went with HATS-993 and which all five
    surfaces then passed ``False`` — a branch no caller reached, removed in HATS-1826.
    """
    sections: list[str] = []

    if result.priorities:
        sections.append(
            "## PRIORITIES\n" + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(result.priorities))
        )

    if result.merged_injection:
        sections.append(result.merged_injection)

    rules_to_deliver: list[tuple[ResolvedComponent, str]] = []
    for rule in result.rules:
        if rule.source_path and rule.source_path.is_dir():
            meta_file = rule.source_path / "metadata.yaml"
            if meta_file.is_file():
                try:
                    meta = RuleMetadata.from_yaml(meta_file)
                    if meta.delivery is not None and meta.delivery not in ("always_on", ""):
                        logger.warning(
                            "rule %r: unrecognized delivery value %r at %s",
                            rule.name,
                            meta.delivery,
                            meta_file,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "rule %r: failed to load metadata at %s: %s",
                        rule.name,
                        meta_file,
                        exc,
                    )

        body = read_rule_body(rule.source_path) if rule.source_path else ""
        if body:
            rules_to_deliver.append((rule, body))
        else:
            logger.warning(
                "rule %r: body is empty or unreadable at %s",
                rule.name,
                rule.source_path,
            )

    if rules_to_deliver:
        rules_section = "## RULES\n"
        for rule, body in rules_to_deliver:
            rules_section += f"\n### {rule.name}\n{body}\n"
        sections.append(rules_section)

    # HATS-1203: project-authored rules, after the framework's own so they
    # read as the more specific layer. Unfiltered — see discover_user_rules.
    user_rules_section = "## USER RULES\n"
    emitted = False
    user_rules = getattr(result, "user_rules", ())
    for rule_path in user_rules:
        try:
            body = rule_path.read_text()
        except OSError as exc:
            logger.warning("user rule %s: unreadable, skipped: %s", rule_path, exc)
            continue
        if body.strip():
            user_rules_section += f"\n### {rule_path.stem}\n{body}\n"
            emitted = True
    if emitted:
        sections.append(user_rules_section)

    return "\n\n".join(sections)


def write_managed_block(prompt_path: Path, content: str, *, project_dir: Path) -> Path:
    """Put ``content`` between the ai-hats markers in ``prompt_path``, keeping the rest.

    Three shapes the file can already be in, and one it can be absent in — the caller
    has decided the path is real, so this only decides what to do with the bytes.
    """
    prompt_path.parent.mkdir(parents=True, exist_ok=True)

    if prompt_path.exists():
        existing = prompt_path.read_text()
        # HATS-284: lowercase scaffold markers signal the project is on
        # the canonical-publish layout — `./CLAUDE.md` is user-owned and
        # the framework injection lives in `.claude/CLAUDE.md`.
        if PUBLISH_AGGREGATOR_START in existing and PUBLISH_AGGREGATOR_END in existing:
            return prompt_path
        if INJECTION_START in existing and INJECTION_END in existing:
            # Update between markers, preserve everything outside
            before = existing[: existing.index(INJECTION_START)]
            after = existing[existing.index(INJECTION_END) + len(INJECTION_END) :]
            new_content = f"{before}{INJECTION_START}\n{content}\n{INJECTION_END}{after}"
            _safe_replace(
                prompt_path,
                new_content.encode("utf-8"),
                reason="system-prompt",
                project_dir=project_dir,
            )
            return prompt_path
        if existing.strip():
            # Existing file without markers — preserve as project context
            _safe_replace(
                prompt_path,
                f"{INJECTION_START}\n{content}\n{INJECTION_END}\n\n{existing}".encode("utf-8"),
                reason="system-prompt",
                project_dir=project_dir,
            )
            return prompt_path

    # Fresh write with markers
    _safe_replace(
        prompt_path,
        f"{INJECTION_START}\n{content}\n{INJECTION_END}\n".encode("utf-8"),
        reason="system-prompt",
        project_dir=project_dir,
    )
    return prompt_path
