"""Tests for script_fetcher module."""
import os
import pytest
from unittest.mock import patch, MagicMock
from pr_ci_dashboard.utils.script_fetcher import fetch_scripts, get_script_path, SCRIPT_DIR


def test_fetch_scripts_returns_script_dir():
    """fetch_scripts returns the script directory when all required scripts exist."""
    result = fetch_scripts()
    assert result == SCRIPT_DIR
    assert isinstance(result, str)


def test_fetch_scripts_raises_on_missing_script(tmp_path, monkeypatch):
    """fetch_scripts raises Exception when a required script is missing."""
    fake_dir = tmp_path / "scripts"
    fake_dir.mkdir()

    # Create only two of three required scripts
    (fake_dir / "e2e-retest.sh").write_text("#!/bin/bash\n")
    (fake_dir / "common.sh").write_text("#!/bin/bash\n")
    # payload-retest.sh is missing

    # Patch SCRIPT_DIR to point to fake directory
    monkeypatch.setattr('pr_ci_dashboard.utils.script_fetcher.SCRIPT_DIR', str(fake_dir))

    with pytest.raises(Exception, match="Missing required script.*payload-retest.sh"):
        fetch_scripts()


def test_fetch_scripts_does_not_chmod(tmp_path, monkeypatch):
    """fetch_scripts does not attempt to change file permissions."""
    fake_dir = tmp_path / "scripts"
    fake_dir.mkdir()

    # Create all required scripts
    for script in ['e2e-retest.sh', 'common.sh', 'payload-retest.sh']:
        (fake_dir / script).write_text("#!/bin/bash\n")

    # Patch SCRIPT_DIR to point to fake directory
    monkeypatch.setattr('pr_ci_dashboard.utils.script_fetcher.SCRIPT_DIR', str(fake_dir))

    # Mock os.chmod to detect if it's called
    with patch('os.chmod') as mock_chmod:
        result = fetch_scripts()
        assert result == str(fake_dir)
        # Verify chmod was never called
        mock_chmod.assert_not_called()


def test_get_script_path():
    """get_script_path returns correct full path to a script."""
    result = get_script_path('e2e-retest.sh')
    assert result == os.path.join(SCRIPT_DIR, 'e2e-retest.sh')
    assert isinstance(result, str)


def test_fetch_scripts_verifies_regular_files(tmp_path, monkeypatch):
    """fetch_scripts raises if a required script is not a regular file."""
    fake_dir = tmp_path / "scripts"
    fake_dir.mkdir()

    # Create valid scripts
    (fake_dir / "e2e-retest.sh").write_text("#!/bin/bash\n")
    (fake_dir / "common.sh").write_text("#!/bin/bash\n")

    # Create payload-retest.sh as a directory instead of a file
    (fake_dir / "payload-retest.sh").mkdir()

    monkeypatch.setattr('pr_ci_dashboard.utils.script_fetcher.SCRIPT_DIR', str(fake_dir))

    with pytest.raises(Exception, match="Missing required script.*payload-retest.sh"):
        fetch_scripts()
