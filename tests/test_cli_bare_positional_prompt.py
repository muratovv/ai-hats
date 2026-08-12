"""Tests for HATS-1202: bare ai-hats positional prompt parsing in _PassthroughGroup."""

from unittest.mock import patch

from click.testing import CliRunner

from ai_hats.cli import main


def test_bare_positional_prompt_passed_to_launch_session() -> None:
    """Bare positional text (e.g. `ai-hats -p agy "hello world"`) should route to _launch_session."""
    runner = CliRunner()
    with patch("ai_hats.cli._launch_session") as mock_launch:
        result = runner.invoke(main, ["-p", "agy", "hello world"])
        assert result.exit_code == 0, result.output
        mock_launch.assert_called_once()
        _, kwargs = mock_launch.call_args
        assert kwargs["provider"] == "agy"
        assert kwargs["extra_args"] == ["hello world"]


def test_bare_positional_unquoted_args_passed_to_launch_session() -> None:
    """Unquoted positional words `ai-hats hello world` pass as list to extra_args."""
    runner = CliRunner()
    with patch("ai_hats.cli._launch_session") as mock_launch:
        result = runner.invoke(main, ["hello", "world"])
        assert result.exit_code == 0, result.output
        mock_launch.assert_called_once()
        _, kwargs = mock_launch.call_args
        assert kwargs["extra_args"] == ["hello", "world"]


def test_registered_subcommands_still_route_normally() -> None:
    """Registered subcommands (e.g. `ai-hats wt ...`) MUST NOT be treated as positional prompts."""
    runner = CliRunner()
    with patch("ai_hats.cli._launch_session") as mock_launch:
        result = runner.invoke(main, ["wt", "--help"])
        assert result.exit_code == 0, result.output
        assert "Manage git worktrees" in result.output
        mock_launch.assert_not_called()


def test_bare_plus_rejected_with_usage_error() -> None:
    """HATS-1456 (S5b): bare '+' in passthrough args is rejected with exit code 2."""
    runner = CliRunner()
    result = runner.invoke(main, ["-r", "assistant", "+", "ai-hats-framework"])
    assert result.exit_code == 2
    assert "bare '+' in provider arguments" in result.output


def test_plus_inside_argument_allowed() -> None:
    """HATS-1456 (S5b): '+' inside another argument (e.g. model name) is not rejected."""
    runner = CliRunner()
    with patch("ai_hats.cli._launch_session") as mock_launch:
        result = runner.invoke(main, ["--model", "gpt-4+turbo"])
        assert result.exit_code == 0, result.output
        mock_launch.assert_called_once()
