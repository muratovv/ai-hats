---
schema_version: 1
type: prompt
block: body
---
## PYTHON DEVELOPMENT

### Standards
- Python 3.11+ with type hints
- Use pyproject.toml for project configuration
- Prefer dataclasses or Pydantic for data models
- Use pathlib.Path over os.path
- Format with ruff, lint with ruff

### Testing
- pytest as test framework
- Tests live in a dedicated `tests/` directory — either mirroring `src/`, or co-located with the module it covers. Pick one per project and hold it.

### Dependencies
- Minimal dependencies — stdlib first
- Pin versions in pyproject.toml
- Use venv for isolation