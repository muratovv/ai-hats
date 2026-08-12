"""Test that every metadata.yaml and config.yaml in the shipped library parses with yaml.safe_load (HATS-1510)."""

from pathlib import Path
import yaml


def test_library_metadata_and_config_yaml_parse():
    library_root = (
        Path(__file__).resolve().parent.parent
        / "packages"
        / "ai-hats-library"
        / "src"
        / "ai_hats_library"
    )
    assert library_root.exists(), f"Library root does not exist: {library_root}"

    yaml_files = sorted(list(library_root.glob("**/*.yaml")))
    assert len(yaml_files) > 0, "No yaml files found in library root"

    failures = []
    for path in yaml_files:
        if path.name not in ("metadata.yaml", "config.yaml"):
            continue
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append((path, str(exc)))

    if failures:
        msg_lines = [f"Failed to parse {len(failures)} YAML file(s) in library:"]
        for path, err in failures:
            msg_lines.append(f"  {path.relative_to(library_root)}: {err}")
        assert False, "\n".join(msg_lines)
