"""Tests for CSRF protection on state-changing API endpoints."""
import pytest
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils.db import init_db


@pytest.fixture
def client(tmp_path):
    """Test client with CSRF ENABLED (unlike other test modules)."""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    app.config['TESTING'] = True
    app.config['CSRF_ENABLED'] = True
    app.config['DB_PATH'] = str(db_path)

    with app.test_client() as client:
        yield client

    app.config['CSRF_ENABLED'] = False


VALID_BODY = {"pr_key": "o/r/1", "enabled": True}


def _get_token(client):
    return client.get('/api/csrf-token').get_json()['token']


def test_post_without_token_rejected(client):
    response = client.post('/api/auto-retest', json=VALID_BODY)
    assert response.status_code == 403
    assert 'CSRF' in response.get_json()['error']


def test_post_with_wrong_token_rejected(client):
    _get_token(client)  # session now has a token
    response = client.post('/api/auto-retest', json=VALID_BODY,
                           headers={'X-CSRF-Token': 'wrong-token'})
    assert response.status_code == 403


def test_post_with_valid_token_accepted(client):
    token = _get_token(client)
    response = client.post('/api/auto-retest', json=VALID_BODY,
                           headers={'X-CSRF-Token': token})
    assert response.status_code == 200


def test_token_without_session_rejected(client):
    """A token from one session is useless without that session's cookie."""
    token = _get_token(client)
    with app.test_client() as other_client:
        response = other_client.post('/api/auto-retest', json=VALID_BODY,
                                     headers={'X-CSRF-Token': token})
    assert response.status_code == 403


def test_get_requests_unaffected(client):
    assert client.get('/api/auto-retest').status_code == 200
    assert client.get('/api/github/oauth/status').status_code == 200
    assert client.get('/').status_code == 200


def test_blueprint_endpoints_protected(client):
    """CSRF applies to blueprint routes (analysis) too."""
    response = client.post('/api/jobs/override', json={"job_url": "https://x"})
    assert response.status_code == 403


def test_token_stable_within_session(client):
    assert _get_token(client) == _get_token(client)
