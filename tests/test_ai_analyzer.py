import pytest
import json
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
        assert args[0] == 'claude-code'
        assert args[1] == 'skill'
        assert args[2] == 'pr-ci-dashboard:detect-permafail'

        # Verify result
        assert result["permafail"] is True
        assert result["reason"] == "TestNetworkPolicy failed in all 3 runs"
        assert len(result["signatures"]) == 3
        assert result["common_tests"] == ["TestNetworkPolicy"]
