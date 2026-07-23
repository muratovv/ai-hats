import pytest
from click.testing import CliRunner
from ai_hats.cli import main

@pytest.mark.integration
def test_provider_hints_in_main_help():
    """Verify that `ai-hats -p <provider> --help` appends the provider hints table."""
    runner = CliRunner()
    
    # We test `python -m ai_hats -p agy --help` equivalent.
    # Since `sys.argv` trick is used in our implementation, we must mock sys.argv
    import unittest.mock
    
    with unittest.mock.patch("sys.argv", ["python", "-m", "ai_hats", "-p", "agy", "--help"]):
        result = runner.invoke(main, ["-p", "agy", "--help"])
        
    assert result.exit_code == 0
    # Check if the title of the table exists
    assert "Provider Hints (agy)" in result.output
    # Check if a specific hint is present
    assert "--model" in result.output
    assert "--headless" in result.output

@pytest.mark.integration
def test_provider_hints_with_role_help():
    """Verify that `ai-hats -r <role> --help` uses the role's provider."""
    runner = CliRunner()
    
    import unittest.mock
    
    # Assuming "architect" role uses claude by default, or agy. Let's test with claude provider
    with unittest.mock.patch("sys.argv", ["python", "-m", "ai_hats", "-r", "architect", "--help"]):
        result = runner.invoke(main, ["-r", "architect", "--help"])
        
    assert result.exit_code == 0
    # Depending on the role's provider, we should see Provider Hints
    assert "Provider Hints" in result.output
