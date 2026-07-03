"""Tests for the audit log and rate limiting."""
import pytest
from unittest.mock import patch
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils import rate_limit
from pr_ci_dashboard.utils.db import init_db, record_audit, get_audit_log


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    app.config['TESTING'] = True
    app.config['CSRF_ENABLED'] = False
    app.config['DB_PATH'] = str(db_path)
    rate_limit.reset()
    with app.test_client() as client:
        yield client
    rate_limit.reset()


PROW = 'https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs'
RETEST_BODY = {"owner": "openshift", "repo": "origin", "pr": 1,
               "jobs": ["e2e-aws"], "type": "e2e"}
ANALYZE_BODY = {"pr": "openshift/origin#1", "repo": "openshift/origin",
                "job_name": "e2e-aws", "job_urls": [f"{PROW}/1", f"{PROW}/2"]}


# ========== rate limiter module ==========

def test_allow_within_limit():
    rate_limit.reset()
    for _ in range(3):
        assert rate_limit.allow('k', 3, 60, now=100.0)
    assert not rate_limit.allow('k', 3, 60, now=100.0)


def test_window_slides():
    rate_limit.reset()
    for i in range(3):
        assert rate_limit.allow('k', 3, 60, now=100.0 + i)
    assert not rate_limit.allow('k', 3, 60, now=103.0)
    # First event ages out of the window
    assert rate_limit.allow('k', 3, 60, now=161.0)


def test_keys_independent():
    rate_limit.reset()
    assert rate_limit.allow('a', 1, 60, now=100.0)
    assert not rate_limit.allow('a', 1, 60, now=100.0)
    assert rate_limit.allow('b', 1, 60, now=100.0)


# ========== audit DB helpers ==========

def test_record_and_get_audit(tmp_path):
    db_path = str(tmp_path / "audit.db")
    init_db(db_path)

    record_audit('user@redhat.com', 'retest', 'openshift/origin#1', 'success', db_path=db_path)
    record_audit('anonymous', 'analyze', 'openshift/origin#1 e2e-aws', 'permafail=True', db_path=db_path)

    entries = get_audit_log(db_path=db_path)
    assert len(entries) == 2
    assert entries[0]['action'] == 'analyze'  # newest first
    assert entries[1]['actor'] == 'user@redhat.com'
    assert entries[1]['result'] == 'success'
    assert entries[0]['timestamp']


def test_record_audit_never_raises(tmp_path):
    """Audit failures must not break the audited operation."""
    record_audit('x', 'y', 'z', 'r', db_path=str(tmp_path / "missing-dir" / "no.db"))


def test_get_audit_log_limit(tmp_path):
    db_path = str(tmp_path / "audit.db")
    init_db(db_path)
    for i in range(5):
        record_audit('a', 'act', f't{i}', 'ok', db_path=db_path)
    assert len(get_audit_log(limit=2, db_path=db_path)) == 2


# ========== endpoint integration ==========

def test_retest_audited_anonymous(client):
    with patch('pr_ci_dashboard.server.retest_jobs', return_value={"success": True}):
        client.post('/api/retest', json=RETEST_BODY)

    entries = client.get('/api/audit').get_json()
    assert entries[0]['actor'] == 'anonymous'
    assert entries[0]['action'] == 'retest'
    assert 'openshift/origin#1' in entries[0]['target']
    assert entries[0]['result'] == 'success'


def test_retest_error_audited(client):
    with patch('pr_ci_dashboard.server.retest_jobs', return_value={"error": "auth_failed"}):
        client.post('/api/retest', json=RETEST_BODY)
    entries = client.get('/api/audit').get_json()
    assert entries[0]['result'] == 'error: auth_failed'


def test_analyze_audited(client):
    with patch('pr_ci_dashboard.api.analysis.analyze_permafail',
               return_value={"permafail": True, "reason": "same test"}):
        client.post('/api/jobs/analyze', json=ANALYZE_BODY)
    entries = client.get('/api/audit').get_json()
    assert entries[0]['action'] == 'analyze'
    assert entries[0]['result'] == 'permafail=True'


def test_override_audited(client):
    client.post('/api/jobs/override', json={"job_url": f"{PROW}/1"})
    entries = client.get('/api/audit').get_json()
    assert entries[0]['action'] == 'override'


def test_retest_rate_limited(client):
    with patch('pr_ci_dashboard.server.retest_jobs', return_value={"success": True}):
        for _ in range(10):
            assert client.post('/api/retest', json=RETEST_BODY).status_code == 200
        response = client.post('/api/retest', json=RETEST_BODY)
    assert response.status_code == 429
    assert 'Rate limit' in response.get_json()['error']


def test_analyze_rate_limited(client):
    with patch('pr_ci_dashboard.api.analysis.analyze_permafail',
               return_value={"permafail": False, "reason": "x"}):
        for _ in range(4):
            assert client.post('/api/jobs/analyze', json=ANALYZE_BODY).status_code == 200
        response = client.post('/api/jobs/analyze', json=ANALYZE_BODY)
    assert response.status_code == 429


def test_rate_limit_per_session(client):
    """A different browser session gets its own budget."""
    with patch('pr_ci_dashboard.server.retest_jobs', return_value={"success": True}):
        for _ in range(10):
            client.post('/api/retest', json=RETEST_BODY)
        assert client.post('/api/retest', json=RETEST_BODY).status_code == 429

        with app.test_client() as other:
            assert other.post('/api/retest', json=RETEST_BODY).status_code == 200


def test_audit_endpoint_invalid_limit(client):
    assert client.get('/api/audit?limit=abc').status_code == 400
