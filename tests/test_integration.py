# tests/test_integration.py
"""Integration tests for the permafail detection workflow"""
import pytest
import json
from unittest.mock import patch
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils.db import init_db


@pytest.fixture
def client(tmp_path):
    """Create test client with temporary database"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    app.config['TESTING'] = True
    app.config['CSRF_ENABLED'] = False
    app.config['DB_PATH'] = str(db_path)

    with app.test_client() as client:
        yield client


def test_full_permafail_workflow(client, tmp_path):
    """Test complete workflow: analyze → detect permafail → check status → override → verify override"""

    # Mock AI analysis to return permafail result
    mock_analysis = {
        "permafail": True,
        "reason": "TestNetworkPolicy/Baseline failed in all runs",
        "signatures": [
            {"type": "test_failure", "tests": ["TestNetworkPolicy/Baseline"]},
            {"type": "test_failure", "tests": ["TestNetworkPolicy/Baseline"]},
            {"type": "test_failure", "tests": ["TestNetworkPolicy/Baseline"]}
        ],
        "common_tests": ["TestNetworkPolicy/Baseline"]
    }

    with patch('pr_ci_dashboard.api.analysis.analyze_permafail', return_value=mock_analysis):
        # Step 1: Trigger analysis via POST /api/jobs/analyze
        job_urls = [
            "https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/1",
            "https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/2",
            "https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/3"
        ]

        response = client.post(
            '/api/jobs/analyze',
            data=json.dumps({
                "pr": "openshift/ovn-kubernetes#1234",
                "repo": "openshift/ovn-kubernetes",
                "job_name": "e2e-aws-ovn",
                "job_urls": job_urls
            }),
            content_type='application/json'
        )

        # Assert analysis endpoint returns 200 and permafail: True
        assert response.status_code == 200
        result = json.loads(response.data)
        assert result["permafail"] is True
        assert "TestNetworkPolicy/Baseline" in result["test_names"]

    # Step 2: Check status via GET /api/jobs/status to verify cached result
    response = client.get(
        '/api/jobs/status?job_urls=' + json.dumps(job_urls)
    )

    assert response.status_code == 200
    status = json.loads(response.data)

    # Verify all 3 URLs have permafail status in database
    for url in job_urls:
        assert url in status
        assert status[url]["permafail"] is True
        assert status[url]["override"] is False
        assert "TestNetworkPolicy/Baseline" in status[url]["reason"]

    # Step 3: Override permafail for first URL via POST /api/jobs/override
    response = client.post(
        '/api/jobs/override',
        data=json.dumps({
            "job_url": job_urls[0]
        }),
        content_type='application/json'
    )

    assert response.status_code == 200
    override_result = json.loads(response.data)
    assert override_result["success"] is True

    # Step 4: Get status again to verify override was set
    response = client.get(
        '/api/jobs/status?job_urls=' + json.dumps(job_urls)
    )

    assert response.status_code == 200
    status = json.loads(response.data)

    # Note: override=1 means user has acknowledged/cleared the permafail
    # Per design doc line 125: "Sets override=true in database"
    # This allows retesting despite permafail detection
    # First URL should have override=True (but permafail analysis result remains)
    assert status[job_urls[0]]["override"] is True
    assert status[job_urls[0]]["permafail"] is True

    # Other URLs should still have override=False
    assert status[job_urls[1]]["override"] is False
    assert status[job_urls[2]]["override"] is False
