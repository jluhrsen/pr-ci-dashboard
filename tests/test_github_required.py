"""Tests for DASHBOARD_REQUIRE_GITHUB and session-token threading into reads."""
import pytest
from unittest.mock import patch
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils.session_store import GITHUB_SESSIONS
from pr_ci_dashboard.utils import github_oauth
from pr_ci_dashboard.utils.db import init_db


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    app.config['TESTING'] = True
    app.config['CSRF_ENABLED'] = False
    app.config['DB_PATH'] = str(db_path)
    GITHUB_SESSIONS.clear()
    with app.test_client() as client:
        yield client
    GITHUB_SESSIONS.clear()


@pytest.fixture
def github_required(monkeypatch):
    monkeypatch.setenv('DASHBOARD_REQUIRE_GITHUB', '1')
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')


def _connect(client):
    with patch.object(github_oauth, 'start_device_flow', return_value={
        'device_code': 'dc', 'user_code': 'X', 'verification_uri': 'u',
        'interval': 5, 'expires_in': 900
    }):
        client.post('/api/github/oauth/start')
    with patch.object(github_oauth, 'poll_device_flow',
                      return_value={'status': 'success', 'token': 'gho_user_tok'}), \
         patch.object(github_oauth, 'get_github_login', return_value='someuser'):
        client.post('/api/github/oauth/poll')


SEARCH_BODY = {"query": "is:pr is:open"}
RETEST_BODY = {"owner": "openshift", "repo": "origin", "pr": 1,
               "jobs": ["e2e-aws"], "type": "e2e"}


# ========== gate behavior ==========

def test_gate_off_by_default(client, monkeypatch):
    monkeypatch.delenv('DASHBOARD_REQUIRE_GITHUB', raising=False)
    with patch('pr_ci_dashboard.server.search_prs', return_value={"prs": [], "total": 0}):
        assert client.post('/api/search', json=SEARCH_BODY).status_code == 200


def test_gate_inert_without_client_id(client, monkeypatch):
    """The flag alone does nothing without GitHub OAuth configured."""
    monkeypatch.setenv('DASHBOARD_REQUIRE_GITHUB', '1')
    monkeypatch.delenv('GITHUB_OAUTH_CLIENT_ID', raising=False)
    with patch('pr_ci_dashboard.server.search_prs', return_value={"prs": [], "total": 0}):
        assert client.post('/api/search', json=SEARCH_BODY).status_code == 200


def test_gate_blocks_gh_endpoints(client, github_required):
    for method, path, body in (
            ('post', '/api/search', SEARCH_BODY),
            ('get', '/api/pr/openshift/origin/1', None),
            ('post', '/api/retest', RETEST_BODY)):
        response = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
        assert response.status_code == 401, path
        assert response.get_json()['error'] == 'github_login_required'


def test_gate_leaves_other_endpoints_alone(client, github_required):
    assert client.get('/api/auto-retest').status_code == 200
    assert client.get('/healthz').status_code == 200
    status = client.get('/api/github/oauth/status')
    assert status.status_code == 200
    assert status.get_json()['login_required'] is True


def test_gate_opens_after_connect_and_recloses(client, github_required):
    assert client.post('/api/search', json=SEARCH_BODY).status_code == 401

    _connect(client)
    with patch('pr_ci_dashboard.server.search_prs', return_value={"prs": [], "total": 0}) as mock_search:
        assert client.post('/api/search', json=SEARCH_BODY).status_code == 200
    assert mock_search.call_args[1]['token'] == 'gho_user_tok'

    client.post('/api/github/oauth/disconnect')
    assert client.post('/api/search', json=SEARCH_BODY).status_code == 401


# ========== token threading ==========

def test_search_uses_session_token_even_without_gate(client, monkeypatch):
    """A connected session's token is used for search regardless of the flag."""
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')
    monkeypatch.delenv('DASHBOARD_REQUIRE_GITHUB', raising=False)
    _connect(client)
    with patch('pr_ci_dashboard.server.search_prs', return_value={"prs": [], "total": 0}) as mock_search:
        client.post('/api/search', json=SEARCH_BODY)
    assert mock_search.call_args[1]['token'] == 'gho_user_tok'


def test_pr_jobs_uses_session_token(client, monkeypatch):
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')
    _connect(client)
    with patch('pr_ci_dashboard.server.get_pr_jobs', return_value={"e2e": {}, "payload": {}}) as mock_jobs:
        client.get('/api/pr/openshift/origin/1')
    assert mock_jobs.call_args[1]['token'] == 'gho_user_tok'


def test_search_prs_env_injection():
    """search_prs puts the token into the gh subprocess env, not argv."""
    from pr_ci_dashboard.api.search import search_prs
    from unittest.mock import MagicMock
    with patch('pr_ci_dashboard.api.search.subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='[]')
        search_prs("is:pr", token='gho_tok')
    assert mock_run.call_args[1]['env']['GH_TOKEN'] == 'gho_tok'
    assert not any('gho_tok' in str(a) for a in mock_run.call_args[0][0])

    with patch('pr_ci_dashboard.api.search.subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='[]')
        search_prs("is:pr")
    assert mock_run.call_args[1]['env'] is None


def test_job_executor_env_injection():
    """Job scripts get the token via env; without one, env is inherited."""
    from pr_ci_dashboard.utils.job_executor import get_e2e_jobs, get_payload_jobs
    from unittest.mock import MagicMock
    for fn in (get_e2e_jobs, get_payload_jobs):
        with patch('pr_ci_dashboard.utils.job_executor.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='{"failed": [], "running": []}')
            fn('openshift/origin', 1, token='gho_tok')
        assert mock_run.call_args[1]['env']['GH_TOKEN'] == 'gho_tok'

        with patch('pr_ci_dashboard.utils.job_executor.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='{"failed": [], "running": []}')
            fn('openshift/origin', 1)
        assert mock_run.call_args[1]['env'] is None
