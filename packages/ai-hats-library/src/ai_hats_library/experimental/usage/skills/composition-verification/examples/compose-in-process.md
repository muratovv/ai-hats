# Compose a role in-process

Reach for this only when `ai-hats config show-prompt --role <role>` is not
enough: you want `errors`, the resolved component list, or a library root other
than the one cwd implies.

Copy it out of this file, set `ROLE`, and run it with the interpreter of the
checkout whose library you mean to compose. It is not shipped as a module on
purpose — this package carries data, and a `.py` importing `ai_hats` would make
the library depend on the engine that reads it.

```python
from pathlib import Path

from ai_hats.assembler import Assembler

ROLE = "<role>"

project = Path(".").resolve()
assembler = Assembler(project)  # this path is what resolves the library

# ALWAYS pass overlays. Without them, user-global customizations
# (~/.ai-hats/customizations.yaml) and the project's own ai-hats.yaml overlays
# are skipped in silence — the usual cause of a false "the rule is not there".
overlays = assembler._get_overlays(ROLE)
result = assembler.composer.compose(ROLE, overlays=overlays)

if result.errors:
    print(f"compose({ROLE!r}) reported errors — the component did NOT resolve:")
    for err in result.errors:
        print(f"    {err}")
    print("\nCheck the name against the library, then re-run:\n\n    ai-hats list traits")
    raise SystemExit(1)

print("traits:", sorted(result.trait_injections))
print("rules :", sorted(c.name for c in result.rules))
print("skills:", sorted(c.name for c in result.skills))
print("injection chars:", len(result.merged_injection))
```
