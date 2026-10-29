---
name: design-system
description: >-
  File an accepted visual direction into the project's one system file,
  and let a page override only itself. Use when a direction has been
  accepted, when the project already has a system file, or when the next
  screen would otherwise invent a second palette.
license: MIT
---

# Design System

One master per project. A page may differ from it. The page does not become a second master.

## When to Use

- Run after the supervisor picks a prototype from `design-direction`.
- Run again at the start of any later screen in the same project.
- The quality pass is `ui-ux-review`.

## Procedure

1. Find the master the screens already read. Names used by the sources this procedure follows: `DESIGN.md`, `MASTER.md`, `tokens.css`, `design-system.md`. The file the screens import wins. When several qualify, name the one you will read and why.
2. Read it before choosing a color or a typeface.
3. When a master exists, write this screen's exception beside the page, in a file a reader can tell belongs to that page. Leave the master as it was.
4. When no master exists, write the picked prototype's additions to `DESIGN.md` at the repository root. Palette, type, and any other addition the prototype names. That write happens after the pick, once. It is the master later screens read.
5. A change to the master itself waits for the person to ask for that change.

Company packages under `nexu-io/open-design` `design-systems/` are reconstructions of other sites. Name them if a brief points at one. Do not copy them in.

Shape, when you need to see one:

- `nextlevelbuilder/ui-ux-pro-max-skill` — a master, a per-page override, and an explicit force flag before the master is replaced.
- `superdesigndev/superdesign-skill` — `DESIGN.md` as context, tokens, motion, and components.
- `nexu-io/open-design` — `DESIGN.md` plus `tokens.css`, as a shape.

## Completion

- The master in force is named, or `DESIGN.md` was just created from an accepted direction.
- This screen's exceptions sit beside the page.
- The master is unchanged unless the person asked to change it.

**Validation scenario (RED).** The repo has a `DESIGN.md` with a type ramp. Asked for the next screen, the agent picks a new typeface and a new hex and saves them as the new look of the product. GREEN: the screen uses the ramp; the exception is a file beside the page; `DESIGN.md` is byte-for-byte the file that was there.

## Anti-Patterns

- Inventing a palette while a master sits unread in the repo.
- Writing the page's exception back into the master.
- Creating a second file that later screens could mistake for the master.
- Creating `DESIGN.md` before the supervisor picks a prototype.

## Attribution

Procedure written for this catalog. The master-plus-page-override move is `nextlevelbuilder/ui-ux-pro-max-skill` (MIT). The `DESIGN.md` shape is `superdesigndev/superdesign-skill` (MIT) and `nexu-io/open-design` (Apache-2.0). Their tables, stacks, and company packages are not copied.
