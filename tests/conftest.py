"""Test configuration for pytest"""
import sys
from pathlib import Path
import pytest
from unittest.mock import patch

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture
def mock_pr_state_open():
    """Mock get_pr_state to OPEN for tests that hit /api/retest past
    validation. Tests that need a different state patch it themselves."""
    with patch('pr_ci_dashboard.server.get_pr_state',
               return_value={"state": "OPEN"}):
        yield
