import pytest
import json
import subprocess
from unittest.mock import patch, MagicMock
from utils.ai_analyzer import analyze_permafail

def test_analyze_permafail_success():
    """Test that analyze_permafail successfully invokes skill and parses output"""
    job_urls = [
        "https://prow.ci.openshift.org/view/gs/123",
        "https://prow.ci.openshift.org/view/gs/456",
        "https://prow.ci.openshift.org/view/gs/789"
    ]
    job_name = "e2e-aws-ovn"
    pr_info = "openshift/ovn-kubernetes#1234"

    # Mock subprocess result
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps({
        "permafail": True,
        "reason": "TestNetworkPolicy failed in all 3 runs",
        "signatures": [
            {"type": "test_failure", "tests": ["TestNetworkPolicy"]},
            {"type": "test_failure", "tests": ["TestNetworkPolicy"]},
            {"type": "test_failure", "tests": ["TestNetworkPolicy"]}
        ],
        "common_tests": ["TestNetworkPolicy"]
    })

    with patch('subprocess.run', return_value=mock_result) as mock_run:
        result = analyze_permafail(job_urls, job_name, pr_info)

        # Verify subprocess was called correctly
        assert mock_run.called
        args = mock_run.call_args[0][0]
        kwargs = mock_run.call_args[1]

        # Verify command arguments
        assert args[0] == 'claude'
        assert args[1] == '--print'

        # Verify prompt was passed via stdin
        assert 'input' in kwargs
        prompt = kwargs['input']
        assert 'pr-ci-dashboard:detect-permafail' in prompt
        assert json.dumps(job_urls) in prompt
        assert job_name in prompt
        assert pr_info in prompt

        # Verify result
        assert result["permafail"] is True
        assert result["reason"] == "TestNetworkPolicy failed in all 3 runs"
        assert len(result["signatures"]) == 3
        assert result["common_tests"] == ["TestNetworkPolicy"]


def test_analyze_permafail_timeout():
    """Test that analyze_permafail handles timeout gracefully"""
    job_urls = ["url1", "url2", "url3"]
    job_name = "e2e-aws-ovn"
    pr_info = "openshift/ovn-kubernetes#1234"

    with patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='test', timeout=300)):
        result = analyze_permafail(job_urls, job_name, pr_info)

        assert result["permafail"] is False
        assert "timed out" in result["error"]
        assert result["signatures"] == []


def test_analyze_permafail_nonzero_exit():
    """Test that analyze_permafail handles non-zero exit code"""
    job_urls = ["url1", "url2", "url3"]
    job_name = "e2e-aws-ovn"
    pr_info = "openshift/ovn-kubernetes#1234"

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "Skill execution error"

    with patch('subprocess.run', return_value=mock_result):
        result = analyze_permafail(job_urls, job_name, pr_info)

        assert result["permafail"] is False
        assert "Skill execution failed" in result["error"]
        assert "Skill execution error" in result["error"]
        assert result["signatures"] == []


def test_analyze_permafail_invalid_json():
    """Test that analyze_permafail handles malformed JSON output"""
    job_urls = ["url1", "url2", "url3"]
    job_name = "e2e-aws-ovn"
    pr_info = "openshift/ovn-kubernetes#1234"

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "not valid json"

    with patch('subprocess.run', return_value=mock_result):
        result = analyze_permafail(job_urls, job_name, pr_info)

        assert result["permafail"] is False
        assert "No JSON found in skill output" in result["error"]
        assert result["signatures"] == []


def test_analyze_permafail_unexpected_error():
    """Test that analyze_permafail handles unexpected exceptions"""
    job_urls = ["url1", "url2", "url3"]
    job_name = "e2e-aws-ovn"
    pr_info = "openshift/ovn-kubernetes#1234"

    with patch('subprocess.run', side_effect=Exception("Unexpected error occurred")):
        result = analyze_permafail(job_urls, job_name, pr_info)

        assert result["permafail"] is False
        assert "Unexpected error" in result["error"]
        assert result["signatures"] == []
