---
name: webapp-testing
description: "Verify a local web app actually renders and behaves in a real browser via Playwright — start the dev server, drive a headless browser, assert on DOM/visible text/navigation, check console errors, capture screenshots. Use as the task_complete gate for any UI change, or to debug 'it compiles but the page is broken'."
---
# Webapp Testing

Confirm a web app renders and behaves in a real browser before calling a UI
change done. **Compiling is not working.** This is the frontend analog of
`go test -race`: an objective "the app loads and the happy path works" signal.

## When to Use
Use as the task_complete gate for any UI change, or to debug integration-level
breakage that unit tests miss — routing, event handlers, hydration, layout,
console errors. Skip for non-UI code, or when a headless unit/integration test
already exercises the exact behavior. This verifies **correctness** (does it
work); for UI **quality** (is it usable/clear) use `ui-ux-review`.

## Procedure (recon → act → assert)
1. **Start the app** — bring up the dev/preview server. Poll the URL until it
   answers; do **not** blind-`sleep`. Tear it down in a `finally`/cleanup block.
2. **Recon** — load the page headless; screenshot + dump the rendered DOM.
   Identify *stable* selectors (role / accessible label / `data-testid`), not
   brittle CSS paths.
3. **Act** — drive the discovered selectors (click, type, navigate) to exercise
   the happy path plus at least one error/edge path.
4. **Assert** — visible text, DOM state, URL after navigation, and key network
   responses. Await locators; never assert on a guessed timing.
5. **Check console** — fail on uncaught errors and unexpected warnings
   (hydration mismatches, failed requests / 404s).
6. **Capture evidence** — screenshot key states for the record.

## Completion (task_complete gate)
- Dev server boots and the target route returns 200 + renders.
- Happy-path interaction asserted green; one error path checked.
- No uncaught console errors.
- Server torn down — no orphan process left running.

## Anti-Patterns
- Asserting on brittle CSS selectors instead of role / label / `data-testid`.
- `sleep N` instead of polling for readiness / awaiting locators.
- Leaving the dev server running after the run.
- Treating a successful build or passing unit tests as proof the page works.

## Attribution
Distilled from **anthropics/skills** `skills/webapp-testing` (Apache-2.0).
Procedure-only — upstream Playwright helper scripts are not vendored.
Provenance in `metadata.yaml` under `upstream:`.
