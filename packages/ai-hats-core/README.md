# ai-hats-core

Core primitives and shared mechanisms for the
[ai-hats](https://github.com/muratovv/ai-hats) framework — **minimal
dependencies, each load-bearing** (currently exactly one: pydantic). No domain
schemas: each ai-hats package owns its own.

It ships five mechanisms:

- **Atomic file writes** — `atomic_write_text` / `atomic_write_bytes`:
  unique-tmp-in-same-dir + `os.replace`, so a reader never observes a
  half-written file and a crash never leaves a truncated one.
- **Composition value-types** — `CompositionResult` / `ResolvedComponent`
  (frozen dataclasses) + the `ComponentKind` enum: the immutable result
  contract of role assembly, composed once by the integrator and injected
  down into packages.
- **YAML model base** — `YamlModel`: pydantic base for
  YAML-round-trippable models (`to_dict` / `from_dict`, `extra="ignore"`).
- **Git-env hygiene** — `scrubbed_git_env()`: `os.environ` copy minus the
  three `GIT_*` plumbing vars that retarget cwd-scoped git subprocesses.
- **Safe deletion** — `ai_hats_core.safe_delete`: trash-bin destructive
  ops (`discard` / `replace`) instead of raw `unlink`/`rmtree`, with a
  per-process session and recovery summary.

## Install

```sh
pip install ai-hats-core
```

Requires Python 3.11+.

## Usage

```python
from pathlib import Path
from ai_hats_core import atomic_write_text, scrubbed_git_env

atomic_write_text(Path("config.json"), '{"ok": true}\n')

import subprocess
subprocess.run(["git", "status"], cwd=Path("."), env=scrubbed_git_env())
```

## Public API

`ai_hats_core.__all__`:

- `atomic_write_text(path, text, *, mode=None)` / `atomic_write_bytes(path, data, *, mode=None)`
- `CompositionResult` / `ResolvedComponent` / `ComponentKind`
- `YamlModel`
- `scrubbed_git_env()`

plus the `ai_hats_core.safe_delete` module namespace.

## Dependencies

`pydantic>=2` — the base of the model layer. Nothing else.

## Versioning

[SemVer](https://semver.org/). The public API is `ai_hats_core.__all__` +
`ai_hats_core.safe_delete`.

## License

MIT. See the [ai-hats repository](https://github.com/muratovv/ai-hats).
