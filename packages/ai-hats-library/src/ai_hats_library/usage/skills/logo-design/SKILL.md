---
name: logo-design
description: >-
  Take a mark from a brief to three different concepts and stop there.
  Use when drawing a new mark, critiquing one, redesigning one, or
  exporting assets from a mark the person has already picked.
license: MIT
---

# Logo Design

Carry one mark to a choice. The kit starts after the person names the concept.

## When to Use

Five modes. Pick from what was asked.

- **New mark** and **redesign** — the procedure below. A redesign brief names what has to survive from the current mark.
- **Critique** — read the SVG, run the audit, name the idea and the category cliché. Draw a replacement only when asked.
- **Assets** — export only after a concept has been picked in this engagement or handed to you as the chosen mark.
- **Identity system** — guides, sub-brands, patterns. Stop and say this skill carries one mark. Offer the mark work that is in scope.

A page that needs a logo placed is not this skill. If the mark is already a file, the page places that file.

## Procedure

1. Write the brief: subject, audience, where the mark has to work, what it must not be. For a redesign, add what survives.
2. Name the category clichés in a sentence. The source is the brief and general knowledge. Mark the sentence unchecked against a corpus of marks: this skill ships none.
3. Draw three concepts that differ in idea. Render each as black SVG on a grid, no live `<text>`, no embedded raster.
4. Run `svg_audit.py` resolved against this skill's directory, `--json`, on each concept. Revise a concept that reports `live-text` or `near-miss-angle` before showing it. Show the audit with the concept.
5. Number the three concepts 1, 2, and 3. Show the three SVGs and stop. The supervisor names the pick by number. A kit, a favicon set, or a guidelines page waits until that pick. Silence is not a pick.
6. After the pick, build the kit with `export_variants.py` and `presentation_board.py` from the same directory.

Show the sheet with `concept_sheet.py` or `preview_sheet.py` from the same directory. When no PNG renderer is available, say so in the same message as the SVG and do not claim you looked at a picture. Do not pass `--refs-industry`: the reference library is not part of this skill, and the flag exits non-zero for that reason.

`svglib.py` and `render_png.py` in the same directory are what the sheet and the audit import. They are not a separate step.

## Completion

- New mark and redesign: three concepts, each with audit output, and a stop before any kit.
- Critique: findings and audit, and no redraw unless one was asked for.
- Assets: a kit of the concept the person named.
- Identity system: a stop, with the one-mark work named.

**Validation scenario (RED).** Asked for three concepts for a new mark, the agent returns a favicon set, a wordmark lockup, and a one-page brand guide in the same turn. GREEN: three black SVGs, audit JSON beside each, and a question naming the pick. The kit is absent.

## Anti-Patterns

- Drawing the category cliché as concept one and two variations of it as concepts two and three.
- Claiming the mark is clear of existing trademarks. Clearance is outside this skill.
- Substituting a monogram on a page that already has a logo file.
- Presenting a raster as reviewed when the renderer was missing.

## Attribution

Procedure written for this catalog. The modes, the pause, and the audit come from `kaankiziltug/logo-design-skill` (MIT). The scripts in this skill's directory carry that attribution in their headers. `assets/library/svg` from the same repository is excluded by its `TRADEMARKS.md`. The fourteen `references/` files stay links:

`kaankiziltug/logo-design-skill`, `skills/logo-design/references/`.

Open one when a step needs the brief, the critique, the redesign, mark types, SVG, type, color, or principles. `library-guide.md` describes the mark corpus this skill does not ship. The procedure above is enough to run without them.
