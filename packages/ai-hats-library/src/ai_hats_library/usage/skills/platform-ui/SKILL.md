---
name: platform-ui
description: >-
  Choose the platform element, the layout, the color scheme, and the
  first paint when building a web UI. Use when adding or changing a
  control, a layout, a theme, or the first screen of a page.
license: MIT
---

# Platform UI

Build the control, the layout, the scheme, and the first screen with the platform.

## When to Use

- Quality of the result is `ui-ux-review`. The scenario gate is `webapp-testing`.
- Naming the schemes on a prototype is `design-direction`. This skill declares the schemes that prototype already named.
- Skip for backend work, scripts, and a change with no HTML, CSS, or first-paint surface.
- A mark is `logo-design`. This skill does not draw one.

## Procedure

1. **Element.** Use the platform element that already does the job. A custom widget covers only the gap that element does not.
   - A modal (it blocks the page) is a `dialog` opened with `showModal`. The page behind is inert, focus stays inside, `Esc` closes it, and focus returns to the opener. Light dismiss, when the design asks for it, is `closedby="any"`.
   - A non-modal overlay is a `popover`.
   - A disclosure is `details` and `summary`.
   - Data collection is a `form`. A change uses `method="POST"`. Every control has a `name`. Related controls sit in a `fieldset` with a `legend`. The submit button names the action.
2. **Layout.** Size the component from its container with `@container`, and the type from the user's font size with `rem`. When the browser chrome changes the visible height, use `dvh`. Give media an `aspect-ratio` so loading does not move the page.
3. **Scheme.** Put the schemes the design named on the root before first paint: a `<meta name="color-scheme">` in the document head and `color-scheme` on `html`. A piece with one scheme declares that one.
4. **First paint.** The first screen's image and font are in the initial HTML. The image is not `loading="lazy"`; it carries `fetchpriority="high"`. A web font uses `font-display: swap`. A script that is not needed for that first paint uses `defer` or `async`. A long list paints the visible rows.
5. **Field error.** `aria-invalid` and the visible error arrive together, when the field matches `:user-invalid`. They wait until the user has left the field.

## Completion

- The element, the layout, the scheme, and the first paint each follow the step that applies. A step that the change does not touch is left as it was.
- `ui-ux-review` still judges the result. `webapp-testing` still gates it.

**Validation scenario (RED).** Asked for a confirm-delete on a chat, the agent builds a `div` overlay, sets the body font in `px`, lazy-loads the first image, and sets `aria-invalid` when an empty required field receives focus. GREEN: a `dialog` opened with `showModal`, type in `rem`, `color-scheme` on `html` before paint, the first image present in the HTML with `fetchpriority="high"`, and `aria-invalid` only once the field matches `:user-invalid`.

## Anti-Patterns

- A custom overlay, menu, or disclosure where `dialog`, `popover`, or `details` already does the job.
- A second color scheme the design did not name.
- A passing `webapp-testing` run treated as the element choice.
- An external guide pasted into the answer. The steps above are the procedure.

## Attribution

Procedure written for this catalog. The element, layout, scheme, and first-paint choices are distilled from `GoogleChrome/modern-web-guidance` (Apache-2.0) at `4f2b13e25c5815b3f080ca0d983e828d96561a0f`, and the first-paint fixes from `ChromeDevTools/chrome-devtools-mcp` `skills/debug-optimize-lcp` (Apache-2.0) at `af2834411a564d366f9cab63efb046d9084f4dba`. Their text is not copied.
