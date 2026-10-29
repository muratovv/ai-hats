---
name: design-direction
description: >-
  Show a numbered prototype and hold it until the supervisor picks.
  Use when starting or redesigning a screen, and when a first pass
  would look the same on any similar brief.
license: MIT
---

# Design Direction

Turn a description into a numbered prototype, and wait until the supervisor picks.

## When to Use

- Filing the picked prototype is `design-system`. The quality pass is `ui-ux-review`.
- A named taste (`high-end-visual-design`, brutalist, minimalist) is a separate call, and only when the brief asks for that style by name.
- Skip when the supervisor already picked a prototype and the task is to file it.

## Procedure

1. Write the description: the subject, who it is for, and the job of this piece. One sentence each. When the brief does not say, ask before filling the gap.
2. Make a prototype of the element you reworked. Name the additions it uses. Palette, type, and each color scheme the piece uses are the usual ones. One scheme is a complete answer. Name any other addition the piece actually rests on.
3. Put a number on the prototype. When you show more than one, number each. The supervisor refers to a prototype by that number.
4. Show the prototype and stop. Integration into the product waits until the supervisor picks a number or names the change. Silence is not a pick.

The prototype stays the proposal until that pick. `design-system` files the pick.

Open these when a line needs the source, and leave them as links:

- `anthropics/skills`, `skills/frontend-design/SKILL.md` — the brief-wins rule and examples of an unchosen default.
- `Leonxlnx/taste-skill`, `skills/taste-skill/references/` — type, color, grid, motion, critique, when the line is about that.
- `nexu-io/open-design`, `craft/typography.md`, `craft/color.md` — when the line is choosing type or color.

The anti-list this role applies is `ui-ux-review`. `craft/anti-ai-slop.md` is the same concern written elsewhere; read it for examples, and do not add its bullets as a second list.

## Completion

- A numbered prototype is shown, with the reworked element and the additions it uses.
- The supervisor has picked a number or named a change. Silence is not a pick.
- The product was not changed in this pass.

**Validation scenario (RED).** Asked for a settings screen on an accounting board, the agent produces a cream editorial hero, a small-caps pill over the title, and a reveal on every section, and integrates that into the product. The brief never asked for an editorial landing. GREEN: prototype 1, the settings screen with the element reworked for the board, the palette and type named, then a stop. No integration yet.

## Anti-Patterns

- Integrating into the product and calling that the prototype.
- Showing several prototypes and not numbering them.
- Treating the pause as optional because the prototype looks finished.
- Shipping the cream editorial, the pill over the headline, or a reveal on every section as the look of a finished piece. Those are the common unchosen defaults, not the whole set.

## Attribution

Procedure written for this catalog. The move comes from `anthropics/skills` `skills/frontend-design` (Apache-2.0) and the direction-then-show step of `Leonxlnx/taste-skill` (MIT). Their text is not copied.
