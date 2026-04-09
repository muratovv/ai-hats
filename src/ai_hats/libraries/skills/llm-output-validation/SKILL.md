---
name: llm-output-validation
description: Post-schema semantic validation checklist for structured LLM output
---
# LLM Output Validation

When an LLM produces structured data, schema validation (Pydantic, JSON Schema,
etc.) only guarantees **format correctness**. It cannot catch semantically wrong
but schema-valid output: hallucinated identifiers, out-of-scope selections,
impossible values. Treat structured LLM output the same way you treat untrusted
user input — validate **meaning**, not just shape.

## When to Use
- Writing code that calls an LLM and parses the response into a data model
- Reviewing code that consumes structured LLM output
- Debugging cases where an LLM returned "valid" but wrong data

## Validation Checklist

Apply these checks **in code, immediately after `model_validate()` / schema
parse**. Raise on failure (loud, not a warning).

### 1. Input Round-Trip
Every identifier in the output must exist in the input. No invented IDs.

- If you passed `bundle_id="001"`, the output must reference `"001"`, not `"002"`.
- If you passed a list of filenames, every filename in the response must appear
  in the original list.
- Implementation: set/dict membership check on every ID field.

### 2. Scope Subset
When the output selects from the input (e.g., "pick the top 3"), verify
`selection ⊆ input`.

- If the input has items `[A, B, C]` and the output picks `[A, D]`, `D` is
  hallucinated.
- Implementation: `assert set(output.selected) <= set(input.candidates)`.

### 3. Value Bounds
Numeric fields must be within the declared range.

- Scores, ratings, confidence levels: check `0 <= v <= max`.
- Counts: non-negative, bounded by input size.
- Implementation: simple range assertions or Pydantic `Field(ge=0, le=10)`.

### 4. Reference Integrity
String references to entities (filenames, function names, URLs, paths) must
resolve in the current context.

- A cited filename must exist on disk (or in the provided listing).
- A cited function must appear in the AST / grep output.
- Implementation: `Path(ref).exists()` or equivalent lookup.

### 5. Semantic Plausibility
Catch generic garbage the LLM emits when confused.

- Non-empty fields where content is required.
- No boilerplate filler ("As an AI language model…", "I'm sorry…").
- Field lengths proportional to expected content (a one-word "summary" of a
  1000-line file is suspicious).
- Implementation: length checks, regex for known filler patterns.

## Where to Put the Check

```python
# Immediately after schema parse — before ANY downstream use.
result = ResponseModel.model_validate(raw)
validate_response_integrity(result, original_input)  # ← HERE
# Now safe to use `result`.
```

Raise `ValueError` (or a domain-specific error) on failure. Do **not** log a
warning and continue — the whole point is to prevent downstream consumers from
acting on garbage.

## Case Study: HATS-066

The judge LLM in the retrospective pipeline was asked to evaluate
`bundle_id="001"`. It returned a schema-valid `JudgeResult` with
`bundle_id="002"` — a hallucinated identifier. Pydantic didn't notice because
`bundle_id` was typed as `str`, not constrained to the input. The fix: a
three-line integrity check after parse that verified `result.bundle_id ==
expected_bundle_id`. Trivial code, but it caught a class of bug that no amount
of prompt engineering could reliably prevent.

## Anti-Patterns
- **Trusting Pydantic schema as a semantic guarantee** — schema validates shape,
  not meaning.
- **Logging a warning instead of raising** — downstream code will still consume
  the bad data, just with a log line nobody reads.
- **Deferring validation to a downstream consumer** — by the time the consumer
  notices, the provenance is lost and the error is harder to debug.
- **Adding constraints only to the prompt** — prompts are suggestions, not
  contracts. Code is the only reliable enforcement.
