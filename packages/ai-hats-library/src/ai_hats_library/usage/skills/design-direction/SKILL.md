---
name: design-direction
description: >
  Record a page's visual direction from its brief and hold it until
  someone has seen it. Use when starting or redesigning a screen, and
  when a first pass would look the same on any similar brief.
license: MIT
---

# Design Direction

Write the direction the page is built from, and wait until a person has answered.

## When to Use

- A mark is `logo-design`. Filing the accepted lines into the project file is `design-system`. The quality pass is `ui-ux-review`.
- A named taste (`high-end-visual-design`, brutalist, minimalist) is a separate call, and only when the brief asks for that style by name.
- Skip when the person handed you an accepted direction and asked you to build the page.

## Procedure

1. Name the subject, who it is for, and the job of this page. One sentence each. When the brief does not say, ask before filling the gap.
2. Write five lines: palette; one or two typefaces and the job of each; the layout move; the one bold element; what stays quiet.
3. Keep a line when the brief is the reason it is there. Replace a line that would have come out the same for any similar brief, or mark it as the brief itself asking for that move.
4. Show the five lines and wait. The page is unwritten until the person accepts them or names the change.

The lines live in the working message until they are accepted. `design-system` files them after that.

Open these when a line needs the source, and leave them as links:

- `anthropics/skills`, `skills/frontend-design/SKILL.md` — the brief-wins rule and examples of an unchosen default.
- `Leonxlnx/taste-skill`, `skills/taste-skill/references/` — type, color, grid, motion, critique, when the line is about that.
- `nexu-io/open-design`, `craft/typography.md`, `craft/color.md` — when the line is choosing type or color.

The anti-list this role applies is `ui-ux-review`. `craft/anti-ai-slop.md` is the same concern written elsewhere; read it for examples, and do not add its bullets as a second list.

## Completion

- Five lines are written, each tied to this brief or replaced.
- The person has accepted them or named a change. Silence is not acceptance.
- No page file was written in this pass.

**Validation scenario (RED).** Asked for a settings screen on an accounting board, the agent produces a cream editorial hero, a small-caps pill over the title, and a reveal on every section, then starts the HTML. The brief never asked for an editorial landing. GREEN: five lines about the board, the accountant, and the job of the screen, then a stop, with no page file yet.

## Anti-Patterns

- Opening the page file and calling the first palette the direction.
- Treating the pause as optional because the direction looks finished.
- Shipping the cream editorial, the pill over the headline, or a reveal on every section as the look of a finished page. Those are the common unchosen defaults, not the whole set.

## Attribution

Procedure written for this catalog. The move comes from `anthropics/skills` `skills/frontend-design` (Apache-2.0) and the direction-then-show step of `Leonxlnx/taste-skill` (MIT). Their text is not copied.
