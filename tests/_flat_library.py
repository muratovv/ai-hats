"""A handwritten library root in the flat form (ADR-0034), for processing tests.

``write_root`` lays files out from a ``{relative path: text}`` mapping;
``SAMPLE`` is the shared fixture: three composites, prompts of three blocks,
one skill with a tree, one operation, one payload file.
"""

from __future__ import annotations

from pathlib import Path

SAMPLE: dict[str, str] = {
    "alpha.yaml": (
        "schema_version: 1\ntype: composite\n"
        "expr: alpha::prompt + alpha::priorities + shared + tool\n"
        "tags: [role, hitl]\nsummary: the sample role\n"
    ),
    "alpha/prompt.md": "---\nschema_version: 1\ntype: prompt\nblock: body\n---\nALPHA\n",
    "alpha/priorities.md": "---\nschema_version: 1\ntype: prompt\nblock: PRIORITIES\n---\n1. Speed",
    "shared.yaml": "schema_version: 1\ntype: composite\nexpr: shared::prompt + rule-one + tool\n",
    "shared/prompt.md": "---\nschema_version: 1\ntype: prompt\nblock: body\n---\nSHARED\n",
    "rules/rule-one.md": (
        "---\nschema_version: 1\ntype: prompt\nblock: RULES\nheading: rule-one\n---\nRULE ONE\n"
    ),
    "bare.yaml": "schema_version: 1\ntype: composite\nexpr: bare::prompt\n",
    "bare/prompt.md": "---\nschema_version: 1\ntype: prompt\nblock: body\n---\nBARE\n",
    "skills/tool/SKILL.md": (
        "---\nname: tool\ndescription: a tool\nai_hats:\n  schema_version: 1\n  type: skill\n"
        "  requires: {cli: [rack]}\n---\n# Tool\n"
    ),
    "skills/tool/scripts/run.sh": "#!/usr/bin/env bash\nexit 0\n",
    "skills/tool/references/notes.md": "# notes, payload of the skill\n",
    "ops/rack.transition.yaml": (
        "schema_version: 1\ntype: operation\nsurface: rack\nreader: rack_transition\n"
        "binds_ticket: true\n"
    ),
    "README.md": "# a payload file without a type\n",
}


def write_root(root: Path, files: dict[str, str] = SAMPLE) -> Path:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root
