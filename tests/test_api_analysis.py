# tests/test_api_analysis.py
import pytest
import json
from unittest.mock import patch
from server import app
from utils.db import init_db

@pytest.fixture
def client(tmp_path):
    """Create test client with temporary database"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    app.config['TESTING'] = True
    app.config['DB_PATH'] = str(db_path)

    with app.test_client() as client:
        yield client

def test_analyze_endpoint_triggers_analysis(client, tmp_path):
    """Test POST /api/jobs/analyze triggers AI analysis and caches result"""
    request_data = {
        "pr": "openshift/ovn-kubernetes#1234",
        "repo": "openshift/ovn-kubernetes",
        "job_name": "e2e-aws-ovn",
        "job_urls": [
            "https://prow.ci.openshift.org/view/1",
            "https://prow.ci.openshift.org/view/2",
            "https://prow.ci.openshift.org/view/3"
        ]
    }

    mock_analysis = {
        "permafail": True,
        "reason": "TestA failed in all runs",
        "signatures": [
            {"type": "test_failure", "tests": ["TestA"]},
            {"type": "test_failure", "tests": ["TestA"]},
            {"type": "test_failure", "tests": ["TestA"]}
        ],
        "common_tests": ["TestA"]
    }

    with patch('api.analysis.analyze_permafail', return_value=mock_analysis):
        response = client.post(
            '/api/jobs/analyze',
            data=json.dumps(request_data),
            content_type='application/json'
        )

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["permafail"] is True
    assert data["reason"] == "TestA failed in all runs"

    # Verify analysis was cached in database
    from utils.db import get_permafail_status
    cached = get_permafail_status(request_data["job_urls"], db_path=str(tmp_path / "test.db"))
    assert len(cached) == 3  # All 3 URLs should be cached
    assert cached[request_data["job_urls"][0]]["permafail"] is True

def test_analyze_endpoint_invalid_json(client):
    """Test endpoint rejects invalid JSON"""
    response = client.post(
        '/api/jobs/analyze',
        data='not valid json',
        content_type='application/json'
    )
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data
    assert "Invalid JSON" in data["error"]

def test_analyze_endpoint_missing_fields(client):
    """Test endpoint rejects missing required fields"""
    # Missing job_urls
    request_data = {
        "pr": "openshift/ovn-kubernetes#1234",
        "repo": "openshift/ovn-kubernetes",
        "job_name": "e2e-aws-ovn"
    }
    response = client.post(
        '/api/jobs/analyze',
        data=json.dumps(request_data),
        content_type='application/json'
    )
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data
    assert "Missing field: job_urls" in data["error"]

def test_analyze_endpoint_wrong_number_of_urls(client):
    """Test endpoint rejects wrong number of job URLs"""
    request_data = {
        "pr": "openshift/ovn-kubernetes#1234",
        "repo": "openshift/ovn-kubernetes",
        "job_name": "e2e-aws-ovn",
        "job_urls": [
            "https://prow.ci.openshift.org/view/1",
            "https://prow.ci.openshift.org/view/2"
        ]
    }
    response = client.post(
        '/api/jobs/analyze',
        data=json.dumps(request_data),
        content_type='application/json'
    )
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data
    assert "Exactly 3 job URLs required" in data["error"]

def test_analyze_endpoint_invalid_pr_format(client):
    """Test endpoint rejects invalid PR format"""
    request_data = {
        "pr": "openshift/ovn-kubernetes",  # Missing #number
        "repo": "openshift/ovn-kubernetes",
        "job_name": "e2e-aws-ovn",
        "job_urls": [
            "https://prow.ci.openshift.org/view/1",
            "https://prow.ci.openshift.org/view/2",
            "https://prow.ci.openshift.org/view/3"
        ]
    }
    response = client.post(
        '/api/jobs/analyze',
        data=json.dumps(request_data),
        content_type='application/json'
    )
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data
    assert "Invalid PR format" in data["error"]

    # Non-integer PR number
    request_data["pr"] = "openshift/ovn-kubernetes#abc"
    response = client.post(
        '/api/jobs/analyze',
        data=json.dumps(request_data),
        content_type='application/json'
    )
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data
    assert "Invalid PR number" in data["error"]

def test_analyze_endpoint_database_failure(client, tmp_path):
    """Test endpoint handles database storage failure"""
    request_data = {
        "pr": "openshift/ovn-kubernetes#1234",
        "repo": "openshift/ovn-kubernetes",
        "job_name": "e2e-aws-ovn",
        "job_urls": [
            "https://prow.ci.openshift.org/view/1",
            "https://prow.ci.openshift.org/view/2",
            "https://prow.ci.openshift.org/view/3"
        ]
    }

    mock_analysis = {
        "permafail": True,
        "reason": "TestA failed",
        "signatures": [],
        "common_tests": ["TestA"]
    }

    with patch('api.analysis.analyze_permafail', return_value=mock_analysis), \
         patch('api.analysis.store_analysis', side_effect=Exception("DB write failed")):
        response = client.post(
            '/api/jobs/analyze',
            data=json.dumps(request_data),
            content_type='application/json'
        )

    assert response.status_code == 500
    data = json.loads(response.data)
    assert "error" in data
    assert "Internal server error" in data["error"]
