"""Read retro files: parse frontmatter, auto-migrate, dispatch to model.

Two file formats are supported:

1. **Frontmatter + body** (session retros, judge retros)
   ```
   ---
   schema: hats-session-retro/v1
   ...
   ---

   markdown body here
   ```

2. **Pure YAML** (bundles)
   ```
   schema: hats-bundle/v1
   bundle_id: BUNDLE-...
   ...
   ```

The dispatch from `schema` field to model class is centralized in
`SCHEMA_FAMILY_TO_MODEL` so adding a new family is one line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import yaml
from pydantic import BaseModel

from .aggregation import AggregationV1
from .bundle import BundleV1
from .judge_retro import JudgeRetroV1
from .session_retro import SessionRetroV1

#: dispatch table — schema family → pydantic model class for the LATEST version
SCHEMA_FAMILY_TO_MODEL: dict[str, type[BaseModel]] = {
    "hats-session-retro": SessionRetroV1,
    "hats-bundle": BundleV1,
    "hats-judge-retro": JudgeRetroV1,
    "hats-aggregation": AggregationV1,
}

#: union type for any retro artifact
RetroArtifact = Union[SessionRetroV1, BundleV1, JudgeRetroV1, AggregationV1]


def parse(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body.

    Returns (frontmatter_dict, body_text). For pure YAML files (no leading
    `---\\n`), body is an empty string.
    """
    if not text.startswith("---\n"):
        loaded = yaml.safe_load(text)
        if loaded is None:
            raise ValueError("Empty or invalid YAML content")
        if not isinstance(loaded, dict):
            raise ValueError(
                f"Top-level YAML must be a mapping, got {type(loaded).__name__}"
            )
        return loaded, ""

    rest = text[len("---\n"):]
    end = rest.find("\n---\n")
    if end == -1:
        # tolerate file ending right after closing marker (no trailing newline)
        if rest.endswith("\n---"):
            return yaml.safe_load(rest[:-len("\n---")]) or {}, ""
        raise ValueError("Malformed frontmatter: missing closing '---'")
    fm_text = rest[:end]
    body = rest[end + len("\n---\n"):]
    loaded = yaml.safe_load(fm_text)
    if loaded is None:
        raise ValueError("Empty frontmatter")
    if not isinstance(loaded, dict):
        raise ValueError(
            f"Frontmatter must be a mapping, got {type(loaded).__name__}"
        )
    return loaded, body


def load(path: Path) -> tuple[RetroArtifact, str]:
    """Load and validate a retro file against its schema family's latest model.

    Returns (model_instance, body). For BundleV1 (pure YAML), body is "".
    The ``schema`` field is a Pydantic ``Literal["family/v1"]`` on each model,
    so wrong-version files fail with a clear validation error — no migration
    layer is interposed.
    """
    raw, body = parse(path.read_text())
    schema = raw.get("schema")
    if not isinstance(schema, str) or "/" not in schema:
        raise ValueError(f"Invalid or missing schema in {path}: {schema!r}")
    family = schema.rsplit("/", 1)[0]
    model_cls = SCHEMA_FAMILY_TO_MODEL.get(family)
    if model_cls is None:
        known = ", ".join(sorted(SCHEMA_FAMILY_TO_MODEL))
        raise ValueError(f"Unknown schema family {family!r}. Known: {known}")
    return model_cls.model_validate(raw), body
