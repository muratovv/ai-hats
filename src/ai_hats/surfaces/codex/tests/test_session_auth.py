import os
from pathlib import Path

import pytest

from ai_hats.materialization import ApplyMaterializer, PlanMaterializer
from ai_hats.surfaces.codex.session_auth import reconcile_auth, stage_auth


@pytest.mark.parametrize(
    "baseline",
    [b"{", b"\xff", b"[]", b"{}", b'{"digest": 3}', b'{"digest": "bad"}'],
)
def test_invalid_baseline_is_reported_without_changing_credentials(
    tmp_path: Path, baseline: bytes
) -> None:
    base, session = tmp_path / "base", tmp_path / "session"
    base.mkdir()
    (base / "auth.json").write_text('{"fixture": "old"}')
    stage_auth(base, session, ApplyMaterializer())
    (session / "auth.json").write_text('{"fixture": "new"}')
    (session / ".ai-hats-auth-baseline.json").write_bytes(baseline)

    with pytest.raises(RuntimeError, match="invalid.*baseline"):
        reconcile_auth(base, session)

    assert (base / "auth.json").read_text() == '{"fixture": "old"}'
    assert (session / "auth.json").read_text() == '{"fixture": "new"}'


@pytest.mark.parametrize("initial", [None, '{"fixture": "old"}'])
def test_login_is_persisted_before_a_new_session_starts(
    tmp_path: Path, initial: str | None
) -> None:
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


def test_dry_run_never_copies_credentials_or_records_their_digest(tmp_path: Path) -> None:
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


def test_write_failure_is_reported_and_keeps_both_copies(tmp_path: Path) -> None:
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
def test_older_session_never_restores_auth_after_another_logout(
    tmp_path: Path, changed: bool
) -> None:
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
def test_reconciliation_refuses_a_substituted_symlink(tmp_path: Path, location: str) -> None:
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


@pytest.mark.parametrize("legacy_auth", [False, True])
def test_home_without_baseline_or_private_auth_can_be_finalized(
    tmp_path: Path, legacy_auth: bool
) -> None:
    base, session = tmp_path / "base", tmp_path / "session"
    base.mkdir()
    session.mkdir()
    shared = base / "auth.json"
    shared.write_text('{"fixture": "old"}')
    if legacy_auth:
        (session / "auth.json").symlink_to(shared)

    assert reconcile_auth(base, session) is None

    assert shared.read_text() == '{"fixture": "old"}'
