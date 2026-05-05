"""Write retro files: serialize model + body to disk as YAML frontmatter + markdown body."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


def dump(model: BaseModel, path: Path, body: str = "") -> None:
    """Write model to path as YAML frontmatter + markdown body.

    Uses `by_alias=True` so the `schema` field (Python keyword) round-trips
    correctly. `exclude_none=True` keeps optional fields out of the output
    when not set.
    """
    data = model.model_dump(by_alias=True, mode="json", exclude_none=True)
    yaml_text = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )

    body_section = body if body.endswith("\n") or not body else body + "\n"
    path.write_text(f"---\n{yaml_text}---\n\n{body_section}")
