"""Derived-views extension: STATE.md regeneration (post-lock, own lock,
atomic replace — HATS-470 heir)."""

from __future__ import annotations

from ai_hats_rack.extensions import DerivedViewsExtension

from rack_testkit import make_kernel, walk


def _kernel(tasks_dir, tmp_path):
    views = DerivedViewsExtension(tasks_dir, tmp_path / "STATE.md")
    return make_kernel(tasks_dir, subscribers=[views]), views


def test_state_md_lists_tasks_with_priority(tasks_dir, tmp_path, cwd):
    kernel, views = _kernel(tasks_dir, tmp_path)
    kernel.create(actor="test", caller_cwd=cwd, task_id="T-1", title="First", priority="high")
    kernel.create(actor="test", caller_cwd=cwd, task_id="T-2", title="Second")
    walk(kernel, "T-1", "plan", cwd=cwd)  # first event → view regenerated

    content = views.state_md_path.read_text()
    assert "T-1" in content and "T-2" in content
    assert "[high]" in content
    assert "## PLAN" in content and "## BRAINSTORM" in content


def test_cancel_appears_in_state_md(tasks_dir, tmp_path, cwd):
    kernel, views = _kernel(tasks_dir, tmp_path)
    kernel.create(actor="test", caller_cwd=cwd, task_id="T-1", title="Drop me")
    kernel.transition("T-1", "cancelled", actor="test", caller_cwd=cwd, resolution="dup")

    content = views.state_md_path.read_text()
    assert "## CANCELLED" in content
    assert "T-1" in content


def test_view_tracks_state_changes(tasks_dir, tmp_path, cwd):
    kernel, views = _kernel(tasks_dir, tmp_path)
    kernel.create(actor="test", caller_cwd=cwd, task_id="T-1", title="Mover")
    walk(kernel, "T-1", "plan", cwd=cwd)
    assert "## PLAN" in views.state_md_path.read_text()
    walk(kernel, "T-1", "blocked", cwd=cwd)
    content = views.state_md_path.read_text()
    assert "## BLOCKED" in content and "## PLAN" not in content


def test_refresh_is_callable_directly(tasks_dir, tmp_path, cwd):
    kernel, views = _kernel(tasks_dir, tmp_path)
    kernel.create(actor="test", caller_cwd=cwd, task_id="T-1", title="Created only")
    views.refresh()  # create fires no edge event; a direct refresh covers it
    assert "T-1" in views.state_md_path.read_text()
