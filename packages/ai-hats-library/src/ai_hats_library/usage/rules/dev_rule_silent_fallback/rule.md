# Rule: No Fallback That Cannot Report

A handler that swallows a failure without reporting it removes the evidence
before any test, log, or metric can see it — so every *other* defect class
behind it becomes invisible too. The defect is not a wide `except`; it is a body
that **cannot carry the failure outward by any mechanism**.

## The shape to write

```python
# ✅ reports — a logger, a domain call, or a returned value all count:
except Exception as exc:
    logger.warning("skew probe failed: %r", exc)
    return False

except Exception as exc:
    handle_rack_error(exc, as_json)          # a domain call reports too
    return

except Exception as exc:
    return False, f"not loadable as a rack card: {exc}"   # so does a value

# ✅ narrow — a precise except documents the expected case:
except UnknownPrefixError:
    return False

# ✅ silence, declared — when nothing may escape, say why AT the site:
except Exception:  # silent-ok: teardown; logging here may itself throw
    pass
```

## The foil to cut

```python
# ❌ nothing in this body can reach a human or a test:
except Exception:
    pass
except Exception:
    return []
except Exception as exc:      # binding a name you never read reports nothing
    return None

# ❌ `# noqa: BLE001` justifies the BREADTH of the catch, never the SILENCE
# ❌ a bare `# silent-ok:` with no reason — the reason is the whole point
```

## Before writing the handler, ask

1. Can anything in this body reach a log, a caller, or a test? → if no, fix it.
2. Is a narrow `except` the honest spelling of what I expect? → prefer it.
3. Is silence genuinely correct here? → `# silent-ok: <reason>`, reason required.
4. **Is there a test that exercises this branch?** → if not, write one now. An
   untested fallback is a branch nobody has ever run.

## What the gate does and does not prove

`scripts/ci-local.sh silent-fallback` (`scripts/check_silent_fallback.py`)
refuses a broad handler whose body holds no call, no `raise`, and no use of the
bound exception, unless the site carries `# silent-ok:`. Ruff `S110`/`S112` hold
the narrower `pass`/`continue` shape as a second contour.

The gate checks **form**. It cannot prove point 4 — that a fallback is covered
by a test — and does not claim to. That one is on you and on review.

Source: 46 inert handlers, nine inside the gate contour itself, six
hiding a live bug.
