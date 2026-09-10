---
name: article-outline
description: Outline before draft — the text's purpose, its Diátaxis type, audience, style forks and sections, agreed before a word of body text. Use when a text (article, doc page, guide, report) is being planned, or when a draft is about to start without an agreed type and reader.
license: MIT
---

# Article Outline

Settle what the text is, whom it serves and how it will be styled before
drafting. The outline is the plan; the draft follows it.

## When to Use

- A text is being planned, or a draft is about to start and nobody has said
  what type it is and who reads it. A draft that already exists is
  `prose-review`'s job.
- With a task card, the outline fills `plan.md` — Requirements carry the
  purpose, type, audience and style forks; Steps carry the section list.
  Without a card it goes to chat and is agreed there before the draft.

## Procedure

1. **Purpose.** One sentence: who reads this and what they leave with. It
   becomes the text's lead.
2. **Type — one Diátaxis quadrant**, held for the whole text:

   | Quadrant    | The reader wants to    | Shape                                           |
   | ----------- | ---------------------- | ----------------------------------------------- |
   | Tutorial    | learn by doing         | ordered steps, one working result, no options   |
   | How-to      | solve a task they have | goal → steps → verification; assumes competence |
   | Reference   | look something up      | one structure per item, complete, no narrative  |
   | Explanation | understand why         | argument: claim → reasons → consequences        |

   A text serving two quadrants is two texts, or one with a named split.
3. **Audience.** One line: what they already know, what they don't, what they
   came to do. It decides which terms get defined and which steps get skipped.
4. **Style forks** — each settled as a line in the plan, before drafting:
   reference format (inline / numbered / footnote); voice (first person /
   impersonal, imperative / descriptive); code blocks (the reader runs / the
   agent ran / illustration only); links (first mention / every mention);
   terms source (a glossary, or "this text defines its own").
5. **Sections.** One heading per reader gain, one line each: what the reader
   gets here. No gain, no section. In a tutorial the sections are the steps;
   in a reference they are the items.
6. **Agree** the outline with the author. Body text starts after that.

## Completion

- Purpose sentence, one quadrant, audience line, style forks and sections
  with gains are written into the plan (or chat) and agreed.
- Validation — RED: asked for "an article about worktrees", the agent starts
  drafting a tutorial-explanation hybrid for an unnamed reader. GREEN: it
  returns the outline first and asks for one decision — the quadrant.

## Anti-Patterns

- Drafting first and outlining backwards.
- "Overview" as a section's gain.
- Two quadrants in one text without a named split.
- Style forks settled silently — each one costs a review round later.
