"""Tests for the mandatory Google login gate (DASHBOARD_REQUIRE_LOGIN)."""
import base64
import json
import time
import pytest
from unittest.mock import patch
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils.session_store import GOOGLE_SESSIONS
from pr_ci_dashboard.utils import google_oauth
from pr_ci_dashboard.utils.db import init_db


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    app.config['TESTING'] = True
    app.config['CSRF_ENABLED'] = False
    app.config['DB_PATH'] = str(db_path)
    GOOGLE_SESSIONS.clear()
    with app.test_client() as client:
        yield client
    GOOGLE_SESSIONS.clear()


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv('DASHBOARD_REQUIRE_LOGIN', '1')
    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_ID', 'cid')
    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_SECRET', 'sec')


def _sign_in(client):
    client.get('/api/google/oauth/login')
    with client.session_transaction() as sess:
        state = sess['google_oauth_state']
    claims = {'email': 'user@redhat.com', 'iss': 'https://accounts.google.com',
              'aud': 'cid', 'exp': time.time() + 3600}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    with patch.object(google_oauth, 'exchange_code', return_value={
        'refresh_token': 'rt', 'access_token': 'at',
        'id_token': f'h.{payload}.s'
    }):
        client.get(f'/api/google/oauth/callback?code=abc&state={state}')


def test_gate_off_by_default(client, monkeypatch):
    monkeypatch.delenv('DASHBOARD_REQUIRE_LOGIN', raising=False)
    assert client.get('/api/auto-retest').status_code == 200


def test_gate_off_without_oauth_config(client, monkeypatch):
    """The flag alone does nothing if Google OAuth isn't configured -
    otherwise the gate would lock everyone out with no way in."""
    monkeypatch.setenv('DASHBOARD_REQUIRE_LOGIN', '1')
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_ID', raising=False)
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_SECRET', raising=False)
    assert client.get('/api/auto-retest').status_code == 200


def test_gate_blocks_api_without_login(client, gate_on):
    for path in ('/api/auto-retest', '/api/default-query', '/api/auth/status'):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.get_json()['error'] == 'login_required'
    assert client.post('/api/search', json={"query": "is:pr"}).status_code == 401
    assert client.post('/api/retest', json={}).status_code == 401
    assert client.post('/api/jobs/analyze', json={}).status_code == 401


def test_gate_exempts_login_flow_and_index(client, gate_on):
    assert client.get('/').status_code == 200
    assert client.get('/api/csrf-token').status_code == 200
    status = client.get('/api/google/oauth/status')
    assert status.status_code == 200
    assert status.get_json()['login_required'] is True
    # Login redirect reachable
    assert client.get('/api/google/oauth/login').status_code == 302


def test_gate_opens_after_login_and_closes_after_logout(client, gate_on):
    assert client.get('/api/auto-retest').status_code == 401

    _sign_in(client)
    assert client.get('/api/auto-retest').status_code == 200
    assert client.get('/api/google/oauth/status').get_json()['connected'] is True

    client.post('/api/google/oauth/disconnect')
    assert client.get('/api/auto-retest').status_code == 401


def test_status_reports_login_not_required_by_default(client, monkeypatch):
    monkeypatch.delenv('DASHBOARD_REQUIRE_LOGIN', raising=False)
    assert client.get('/api/google/oauth/status').get_json()['login_required'] is False
