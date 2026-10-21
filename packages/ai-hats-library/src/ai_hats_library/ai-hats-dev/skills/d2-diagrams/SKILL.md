---
name: d2-diagrams
description: How a d2 diagram in this repo is drawn and rendered. Use when editing a `.d2` file, `docs/assets/diagrams/_palette.d2`, or `docs/assets/diagrams/render.sh`.
license: MIT
---

# d2 diagrams

## When to Use

Editing or adding a file under `docs/assets/diagrams/`. Not mermaid, and not
the release checklist — that stays on the maintainer role.

## Diagrams (d2)

Sources live at `docs/assets/diagrams/*.d2`; the brand-light palette is
shared via `docs/assets/diagrams/_palette.d2` (imported in every diagram
as `vars: @./_palette`). Render with `bash docs/assets/diagrams/render.sh
[<stem>]` — the script auto-extracts Source Code Pro Medium / SemiBold /
Medium-Italic statics to `$TMPDIR/ai-hats-fonts/` on first run. Commit
both `.d2` source and `.svg` artifact; do NOT commit `.png`. Full
toolchain + shape conventions: `CONTRIBUTING.md#diagrams`.

**Multiline labels.** Inside default or double-quoted label strings,
`\n` works for plain nodes:

    node: First line\nSecond line
    node2: "First line\nSecond line"

Single-quoted strings do NOT interpret escapes — `'foo\nbar'` renders the
literal `\n` characters. The `|md ... |` block syntax is reliable for
`shape: callout` but fragile on plain nodes in sketch mode (labels often
collapse to a single line) — prefer `\n` for plain nodes.

**Theme overrides.** `vars.d2-config.theme-overrides` applies to both
light and dark `theme-id` standalone. The separate `dark-theme-overrides`
field applies only when a light theme is the default and a dark theme is
set as an alternate — using it for `theme-id: 200` alone silently leaves
the Catppuccin Mocha base in place on the first attempt.

**Palette slot map** (16 slots from `_palette.d2`): `N1`–`N7` neutrals
(text → cream background `#faf2e6`), `B1`–`B6` navy spectrum (box fills,
arrow strokes), `AA2/AA4/AA5` brand-orange variants (`AA4 = #e8632b`,
used for cylinder/oval storage shapes), `AB4/AB5` cream/beige (soft
accent parallelograms).

**Containers + nested direction.** `direction: <inner>` inside a container
can be silently ignored by the ELK layout engine when it conflicts with
the outer direction. The backlog FSMs were once attempted as one diagram
with three containers → unreadable; resolved by splitting into 3 separate
`.d2` files. Prefer **separate files** for independent diagrams when more
than ~10 nodes are involved.

## Validation scenario

RED: asked to add a second line to a plain node, the agent writes
`'foo\nbar'`. The render shows the characters `\n`. The fix is a default
or double-quoted label, where `\n` is a newline.

## Completion

- The diagram uses the shared palette and renders with `render.sh`.
- A multiline plain-node label is not single-quoted.
- Both the `.d2` source and the `.svg` are committed. No `.png`.

## Anti-Patterns

- Single-quoted `\n` — the escape is drawn literally.
- `dark-theme-overrides` on a standalone dark `theme-id` — the Mocha base stays.
- One file of independent diagrams — ELK drops a nested `direction`.
