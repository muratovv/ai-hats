"""Library format-schema version-guard (HATS-876 / T18, ADR-0014 §5)."""

from __future__ import annotations

import pytest

from ai_hats.library_schema import (
    SUPPORTED_LIBRARY_SCHEMA,
    LibrarySchemaError,
    check_library_schema,
    read_library_schema_version,
)


def _lib(root, version=None):
    for layer in ("core", "usage"):
        (root / layer).mkdir(parents=True)
    if version is not None:
        (root / "manifest.yaml").write_text(f"schema_version: {version}\n", encoding="utf-8")
    return root


def test_read_version_from_manifest(tmp_path):
    assert read_library_schema_version(_lib(tmp_path, version=3)) == 3


def test_read_version_absent_manifest_is_baseline(tmp_path):
    assert read_library_schema_version(_lib(tmp_path)) == 1


def test_read_version_malformed_manifest_is_baseline(tmp_path):
    root = _lib(tmp_path)
    (root / "manifest.yaml").write_text(": not valid yaml :\n", encoding="utf-8")
    assert read_library_schema_version(root) == 1


def test_check_passes_on_supported_and_none(tmp_path):
    check_library_schema(_lib(tmp_path, version=SUPPORTED_LIBRARY_SCHEMA))  # no raise
    check_library_schema(None)  # broken install is a no-op


def test_check_fails_loud_on_newer(tmp_path):
    root = _lib(tmp_path, version=SUPPORTED_LIBRARY_SCHEMA + 1)
    with pytest.raises(LibrarySchemaError, match="self update"):
        check_library_schema(root)


def test_assembler_fails_loud_on_too_new_builtin(tmp_path, monkeypatch):
    """The guard is wired into composition: a too-new AI_HATS_LIBRARY_ROOT built-in
    stops Assembler construction loudly rather than composing a mismatched library.
    """
    from ai_hats.assembler import Assembler
    from ai_hats.config import ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG

    builtin = _lib(tmp_path / "lib", version=SUPPORTED_LIBRARY_SCHEMA + 1)
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(builtin))

    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig().save(project / PROJECT_CONFIG)

    with pytest.raises(LibrarySchemaError):
        Assembler(project)
