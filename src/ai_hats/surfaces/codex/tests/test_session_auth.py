from pathlib import Path
import os

import pytest

from ai_hats.materialization import ApplyMaterializer, PlanMaterializer
from ai_hats.surfaces.codex.session_auth import reconcile_auth, stage_auth


@pytest.mark.parametrize("initial", [None, '{"fixture": "old"}'])
def test_login_is_persisted_before_a_new_session_starts(tmp_path: Path, initial: str | None):
    base, session = tmp_path / "base", tmp_path / "session"
    base.mkdir()
    if initial is not None:
        (base / "auth.json").write_text(initial)
    stage_auth(base, session, ApplyMaterializer())
    (session / "auth.json").write_text('{"fixture": "renewed"}')

    assert reconcile_auth(base, session) is None

    assert (base / "auth.json").read_text() == '{"fixture": "renewed"}'
    assert (base / "auth.json").stat().st_mode & 0o777 == 0o600
    next_session = tmp_path / "next"
    stage_auth(base, next_session, ApplyMaterializer())
    assert (next_session / "auth.json").read_bytes() == (base / "auth.json").read_bytes()


def test_dry_run_never_copies_credentials_or_records_their_digest(tmp_path: Path):
    base, session = tmp_path / "base", tmp_path / "session"
    base.mkdir()
    (base / "auth.json").write_text('{"fixture": "private"}')
    preview = PlanMaterializer()
    stage_auth(base, session, preview)
    assert not session.exists()
    assert not (base / ".ai-hats").exists()
    apply = ApplyMaterializer()
    stage_auth(base, session, apply)
    assert preview.plan.entries == apply.plan.entries
    assert all(entry.digest is None for entry in apply.plan.entries)
    assert (session / "auth.json").stat().st_mode & 0o777 == 0o600


def test_write_failure_is_reported_and_keeps_both_copies(tmp_path: Path):
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory write permissions")
    base, session = tmp_path / "base", tmp_path / "session"
    base.mkdir()
    (base / "auth.json").write_text('{"fixture": "old"}')
    stage_auth(base, session, ApplyMaterializer())
    (session / "auth.json").write_text('{"fixture": "new"}')

    base.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            reconcile_auth(base, session)
    finally:
        base.chmod(0o700)

    assert (base / "auth.json").read_text() == '{"fixture": "old"}'
    assert (session / "auth.json").read_text() == '{"fixture": "new"}'


@pytest.mark.parametrize("changed", [False, True])
def test_older_session_never_restores_auth_after_another_logout(tmp_path: Path, changed: bool):
    base, session = tmp_path / "base", tmp_path / "session"
    base.mkdir()
    (base / "auth.json").write_text('{"fixture": "old"}')
    stage_auth(base, session, ApplyMaterializer())
    (base / "auth.json").unlink()  # safe-delete: ok synthetic logout fixture
    if changed:
        (session / "auth.json").write_text('{"fixture": "renewed"}')

    warning = reconcile_auth(base, session)

    assert bool(warning) is changed
    assert not (base / "auth.json").exists()


@pytest.mark.parametrize("location", ["base", "session"])
def test_reconciliation_refuses_a_substituted_symlink(tmp_path: Path, location: str):
    base, session = tmp_path / "base", tmp_path / "session"
    base.mkdir()
    (base / "auth.json").write_text('{"fixture": "old"}')
    stage_auth(base, session, ApplyMaterializer())
    target = tmp_path / "unrelated"
    target.write_text("untouched")
    replaced = tmp_path / location / "auth.json"
    replaced.unlink()  # safe-delete: ok synthetic credential fixture
    replaced.symlink_to(target)

    with pytest.raises(RuntimeError, match="symlinked"):
        reconcile_auth(base, session)

    assert target.read_text() == "untouched"
