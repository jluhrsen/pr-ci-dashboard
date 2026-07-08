"""Tests for per-user GitHub OAuth (device flow module, endpoints, retest token pass-through)."""
import pytest
from unittest.mock import patch, MagicMock
from pr_ci_dashboard.server import app, GITHUB_SESSIONS, PENDING_DEVICE_FLOWS
from pr_ci_dashboard.utils import github_oauth
from pr_ci_dashboard.utils.db import init_db


@pytest.fixture
def client(tmp_path):
    """Test client with temp DB and clean session state."""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    app.config['TESTING'] = True
    app.config['CSRF_ENABLED'] = False
    app.config['DB_PATH'] = str(db_path)
    GITHUB_SESSIONS.clear()
    PENDING_DEVICE_FLOWS.clear()

    with app.test_client() as client:
        yield client

    GITHUB_SESSIONS.clear()
    PENDING_DEVICE_FLOWS.clear()


# ========== github_oauth module ==========

def test_get_client_id_unset(monkeypatch):
    monkeypatch.delenv('GITHUB_OAUTH_CLIENT_ID', raising=False)
    assert github_oauth.get_client_id() is None


def test_get_client_id_set(monkeypatch):
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'Iv1.abc123')
    assert github_oauth.get_client_id() == 'Iv1.abc123'


def test_poll_device_flow_states():
    """poll_device_flow maps GitHub responses to status dicts."""
    cases = [
        ({'access_token': 'gho_x'}, {'status': 'success', 'token': 'gho_x'}),
        ({'error': 'authorization_pending'}, {'status': 'pending'}),
        ({'error': 'slow_down', 'interval': 12}, {'status': 'slow_down', 'interval': 12}),
    ]
    for response, expected in cases:
        with patch.object(github_oauth, '_post_form', return_value=response):
            assert github_oauth.poll_device_flow('cid', 'dc') == expected

    with patch.object(github_oauth, '_post_form', return_value={'error': 'expired_token', 'error_description': 'expired'}):
        result = github_oauth.poll_device_flow('cid', 'dc')
        assert result['status'] == 'error'
        assert 'expired' in result['error']


def test_start_device_flow_error():
    """start_device_flow raises when GitHub rejects the client_id."""
    with patch.object(github_oauth, '_post_form', return_value={'error': 'unauthorized_client'}):
        with pytest.raises(RuntimeError):
            github_oauth.start_device_flow('bad-cid')


# ========== API endpoints ==========

def test_status_disabled(client, monkeypatch):
    """Feature reports disabled when GITHUB_OAUTH_CLIENT_ID is unset."""
    monkeypatch.delenv('GITHUB_OAUTH_CLIENT_ID', raising=False)
    status = client.get('/api/github/oauth/status').get_json()
    assert status == {"enabled": False, "connected": False, "login": None, "login_required": False, "bot_active": False}


def test_start_requires_client_id(client, monkeypatch):
    monkeypatch.delenv('GITHUB_OAUTH_CLIENT_ID', raising=False)
    response = client.post('/api/github/oauth/start')
    assert response.status_code == 400


def test_poll_without_flow(client, monkeypatch):
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')
    response = client.post('/api/github/oauth/poll')
    assert response.status_code == 400


def test_full_device_flow(client, monkeypatch):
    """start -> pending poll -> success poll -> status connected."""
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')

    with patch.object(github_oauth, 'start_device_flow', return_value={
        'device_code': 'dc123', 'user_code': 'ABCD-1234',
        'verification_uri': 'https://github.com/login/device',
        'interval': 5, 'expires_in': 900
    }):
        flow = client.post('/api/github/oauth/start').get_json()
    assert flow['user_code'] == 'ABCD-1234'
    assert 'device_code' not in flow  # stays server-side

    with patch.object(github_oauth, 'poll_device_flow', return_value={'status': 'pending'}):
        assert client.post('/api/github/oauth/poll').get_json() == {'status': 'pending'}

    with patch.object(github_oauth, 'poll_device_flow', return_value={'status': 'success', 'token': 'gho_tok'}), \
         patch.object(github_oauth, 'get_github_login', return_value='jluhrsen'):
        result = client.post('/api/github/oauth/poll').get_json()
    assert result == {'status': 'success', 'login': 'jluhrsen'}

    status = client.get('/api/github/oauth/status').get_json()
    assert status == {"enabled": True, "connected": True, "login": "jluhrsen", "login_required": False, "bot_active": False}

    # Disconnect drops the session
    client.post('/api/github/oauth/disconnect')
    status = client.get('/api/github/oauth/status').get_json()
    assert status['connected'] is False


def test_poll_terminal_error_clears_flow(client, monkeypatch):
    """A denied/expired flow is cleared so subsequent polls 400."""
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')
    with patch.object(github_oauth, 'start_device_flow', return_value={
        'device_code': 'dc', 'user_code': 'X', 'verification_uri': 'u'
    }):
        client.post('/api/github/oauth/start')

    with patch.object(github_oauth, 'poll_device_flow', return_value={'status': 'error', 'error': 'denied'}):
        result = client.post('/api/github/oauth/poll').get_json()
    assert result['status'] == 'error'

    assert client.post('/api/github/oauth/poll').status_code == 400


# ========== retest token pass-through ==========

def test_retest_uses_session_token(client, monkeypatch):
    """When a session has a GitHub token, /api/retest posts with it."""
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')

    # Connect a session
    with patch.object(github_oauth, 'start_device_flow', return_value={
        'device_code': 'dc', 'user_code': 'X', 'verification_uri': 'u'
    }):
        client.post('/api/github/oauth/start')
    with patch.object(github_oauth, 'poll_device_flow', return_value={'status': 'success', 'token': 'gho_user_tok'}), \
         patch.object(github_oauth, 'get_github_login', return_value='someuser'):
        client.post('/api/github/oauth/poll')

    with patch('pr_ci_dashboard.server.retest_jobs', return_value={"success": True}) as mock_retest:
        response = client.post('/api/retest', json={
            "owner": "openshift", "repo": "origin", "pr": 1, "jobs": ["e2e-aws"], "type": "e2e"
        })
    assert response.status_code == 200
    assert mock_retest.call_args[1]['token'] == 'gho_user_tok'


def test_retest_without_session_token(client):
    """Without a connected session, retest falls back to no token (pod GH_TOKEN)."""
    with patch('pr_ci_dashboard.server.retest_jobs', return_value={"success": True}) as mock_retest:
        client.post('/api/retest', json={
            "owner": "openshift", "repo": "origin", "pr": 1, "jobs": ["e2e-aws"], "type": "e2e"
        })
    assert mock_retest.call_args[1]['token'] is None


def test_post_retest_comment_env_override():
    """post_retest_comment injects GH_TOKEN into the subprocess env when token given."""
    from pr_ci_dashboard.utils.gh_auth import post_retest_comment

    with patch('pr_ci_dashboard.utils.gh_auth.subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        post_retest_comment('openshift', 'origin', 1, '/test e2e-aws', token='gho_user_tok')

    env = mock_run.call_args[1]['env']
    assert env['GH_TOKEN'] == 'gho_user_tok'

    # Without a token, env is not overridden (inherits process env)
    with patch('pr_ci_dashboard.utils.gh_auth.subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        post_retest_comment('openshift', 'origin', 1, '/test e2e-aws')
    assert mock_run.call_args[1]['env'] is None


# ========== server-side expiry/cleanup ==========

def _connect_session(client):
    """Helper: drive a session to connected state with a mocked flow."""
    with patch.object(github_oauth, 'start_device_flow', return_value={
        'device_code': 'dc', 'user_code': 'X', 'verification_uri': 'u',
        'interval': 5, 'expires_in': 900
    }):
        client.post('/api/github/oauth/start')
    with patch.object(github_oauth, 'poll_device_flow', return_value={'status': 'success', 'token': 'gho_tok'}), \
         patch.object(github_oauth, 'get_github_login', return_value='someuser'):
        client.post('/api/github/oauth/poll')


def test_expired_pending_flow_rejected(client, monkeypatch):
    """Polling an expired device flow returns 400 and clears server state."""
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')
    with patch.object(github_oauth, 'start_device_flow', return_value={
        'device_code': 'dc', 'user_code': 'X', 'verification_uri': 'u',
        'interval': 5, 'expires_in': 900
    }):
        client.post('/api/github/oauth/start')

    # Force the pending flow into the past
    for flow in PENDING_DEVICE_FLOWS.values():
        flow['expires_at'] = 1.0

    assert client.post('/api/github/oauth/poll').status_code == 400
    assert PENDING_DEVICE_FLOWS == {}


def test_expired_session_pruned(client, monkeypatch):
    """An idle connected session past TTL is pruned: status disconnected, retest falls back."""
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')
    _connect_session(client)
    assert client.get('/api/github/oauth/status').get_json()['connected'] is True

    # Age the session past the TTL
    for github in GITHUB_SESSIONS.values():
        github['last_seen'] = 1.0

    status = client.get('/api/github/oauth/status').get_json()
    assert status['connected'] is False
    assert GITHUB_SESSIONS == {}

    with patch('pr_ci_dashboard.server.retest_jobs', return_value={"success": True}) as mock_retest:
        client.post('/api/retest', json={
            "owner": "openshift", "repo": "origin", "pr": 1, "jobs": ["e2e-aws"], "type": "e2e"
        })
    assert mock_retest.call_args[1]['token'] is None


def test_active_session_ttl_slides(client, monkeypatch):
    """Activity refreshes last_seen so an in-use session is not pruned."""
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')
    _connect_session(client)

    # Nearly expired, then activity slides the window
    import time as time_mod
    from pr_ci_dashboard.server import GITHUB_SESSION_TTL
    for github in GITHUB_SESSIONS.values():
        github['last_seen'] = time_mod.time() - GITHUB_SESSION_TTL + 60

    assert client.get('/api/github/oauth/status').get_json()['connected'] is True
    for github in GITHUB_SESSIONS.values():
        assert github['last_seen'] > time_mod.time() - 5
