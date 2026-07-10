# UX canon (reference)

Deep-dive backing for `ui-ux-review`. Distilled from oil-oil/oiloil-ui-ux-guide
(Apache-2.0) and wondelai/skills (MIT) — adapted, not copied. Load on demand;
the 10 hard rules in SKILL.md are the operational summary.

## Refactoring UI — practical visual design

- **Grayscale first.** Design layout, hierarchy, and spacing in black/white/gray
  before adding any color. If it works in grayscale, color only enhances it.
- **Spacing scale.** Pick a scale (4, 8, 12, 16, 24, 32, 48, 64…) and use only
  those steps. Consistent rhythm reads as "designed"; arbitrary px reads as noise.
- **Type scale.** A small fixed set of sizes (e.g. 12/14/16/20/24/30/36). Vary
  weight and color for hierarchy more than size. Body 16px, line-height ~1.5,
  line length 45–75 characters.
- **Emphasize by de-emphasizing.** To make something stand out, mute its
  neighbors rather than shouting louder everywhere.
- **Color in HSL.** Build palettes by fixing hue and varying saturation/lightness.
  Need ~5–10 shades per accent for borders, backgrounds, text, states.
- **Depth via shadow, not borders.** Soft shadows + subtle background shifts
  convey elevation; heavy borders fragment a layout. Light comes from above.
- **Don't rely on color alone** to convey meaning (color-blind users) — pair with
  icon, text, or shape.

## Nielsen's 10 usability heuristics

1. Visibility of system status — keep users informed with timely feedback.
2. Match between system and the real world — speak the users' language.
3. User control and freedom — clear exits, undo/redo.
4. Consistency and standards — follow platform and internal conventions.
5. Error prevention — design out error-prone conditions.
6. Recognition rather than recall — make options/actions visible.
7. Flexibility and efficiency of use — accelerators for experts, simple for novices.
8. Aesthetic and minimalist design — no irrelevant/competing information.
9. Help users recognize, diagnose, recover from errors — plain language, a way out.
10. Help and documentation — easy to search, task-focused, concise.

## Web typography

- Line length 45–75ch; line-height 1.4–1.6 for body, tighter for headings.
- Limit to 1–2 families and a few weights. Establish vertical rhythm on the
  spacing scale. Left-align long text; avoid justified (rivers) and all-caps
  for body.

## Microinteractions

A microinteraction = **trigger → rules → feedback → loops/modes**. Use them for:
state changes (toggles), progress, and confirming actions. Keep them fast
(<300ms), purposeful, and respect `prefers-reduced-motion`. Motion should
direct attention or show causality — never decoration that delays the user.

## Lean UX mindset

Frame work as **assumptions → hypothesis → smallest test → learn**. Prefer the
cheapest experiment that validates a design decision (prototype, one screen,
a/b) over polishing an unvalidated idea. Outcomes over output.

## Anti-AI self-check (common LLM-generated UI tells)

- Uniform spacing everywhere (no grouping/hierarchy) → apply proximity + scale.
- Every element same visual weight, multiple competing CTAs → pick one primary.
- Rainbow palette / gratuitous gradients → grayscale-first, constrain accents.
- Placeholder text used as the only label → add real labels.
- No empty / loading / error states → design all three.
- Centered numeric columns, unbounded tables → right-align numbers, paginate.
