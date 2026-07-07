"""Tests for GitHub App bot-token minting and endpoint wiring."""
import base64
import json
import subprocess
import time
import pytest
from unittest.mock import patch
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils import github_app
from pr_ci_dashboard.utils.db import init_db


@pytest.fixture
def rsa_key(tmp_path):
    """Real RSA private key for JWT signing tests."""
    key_path = tmp_path / "app-key.pem"
    subprocess.run(['openssl', 'genrsa', '-out', str(key_path), '2048'],
                   check=True, capture_output=True)
    return str(key_path)


@pytest.fixture
def bot_env(monkeypatch, rsa_key):
    monkeypatch.setenv('GITHUB_APP_ID', '1460951')
    monkeypatch.setenv('GITHUB_APP_PRIVATE_KEY_FILE', rsa_key)
    github_app.reset_cache()
    yield
    github_app.reset_cache()


@pytest.fixture
def client(tmp_path):
    from pr_ci_dashboard.utils.session_store import GITHUB_SESSIONS
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    app.config['TESTING'] = True
    app.config['CSRF_ENABLED'] = False
    app.config['DB_PATH'] = str(db_path)
    GITHUB_SESSIONS.clear()
    with app.test_client() as client:
        yield client
    GITHUB_SESSIONS.clear()


# ========== configured() ==========

def test_not_configured_by_default(monkeypatch):
    monkeypatch.delenv('GITHUB_APP_ID', raising=False)
    monkeypatch.delenv('GITHUB_APP_PRIVATE_KEY_FILE', raising=False)
    assert not github_app.configured()


def test_not_configured_when_key_file_missing(monkeypatch):
    monkeypatch.setenv('GITHUB_APP_ID', '1460951')
    monkeypatch.setenv('GITHUB_APP_PRIVATE_KEY_FILE', '/nonexistent/key.pem')
    assert not github_app.configured()


def test_configured_with_key(bot_env):
    assert github_app.configured()


# ========== JWT ==========

def test_make_jwt_shape_and_signature(rsa_key, tmp_path):
    jwt = github_app.make_jwt('1460951', rsa_key, now=1_800_000_000)
    header_b64, payload_b64, sig_b64 = jwt.split('.')

    def unb64(s):
        return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))

    assert json.loads(unb64(header_b64)) == {"alg": "RS256", "typ": "JWT"}
    payload = json.loads(unb64(payload_b64))
    assert payload == {"iat": 1_800_000_000 - 60,
                       "exp": 1_800_000_000 + 540, "iss": 1460951}

    # Verify the signature with the public key half
    pub = tmp_path / "pub.pem"
    subprocess.run(['openssl', 'rsa', '-in', rsa_key, '-pubout', '-out', str(pub)],
                   check=True, capture_output=True)
    sig_file = tmp_path / "sig.bin"
    sig_file.write_bytes(unb64(sig_b64))
    verify = subprocess.run(
        ['openssl', 'dgst', '-sha256', '-verify', str(pub), '-signature', str(sig_file)],
        input=f'{header_b64}.{payload_b64}'.encode(), capture_output=True)
    assert verify.returncode == 0, verify.stderr


def test_make_jwt_bad_key_raises(tmp_path):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a key")
    with pytest.raises(RuntimeError):
        github_app.make_jwt('1', str(bad))


# ========== token minting + cache ==========

def _fake_api_factory(expires_in_s=3600, token='ghs_bot_tok'):
    from datetime import datetime, UTC, timedelta
    expires = (datetime.now(UTC) + timedelta(seconds=expires_in_s)) \
        .strftime('%Y-%m-%dT%H:%M:%SZ')
    calls = []

    def fake_api(url, jwt, method='GET'):
        calls.append((url, method))
        if url.endswith('/installation'):
            return {"id": 424242}
        if url.endswith('/access_tokens'):
            return {"token": token, "expires_at": expires}
        raise AssertionError(f"unexpected url {url}")
    fake_api.calls = calls
    return fake_api


def test_get_bot_token_mints_and_caches(bot_env):
    fake = _fake_api_factory()
    with patch.object(github_app, '_api', side_effect=fake) as mock_api:
        assert github_app.get_bot_token() == 'ghs_bot_tok'
        assert github_app.get_bot_token() == 'ghs_bot_tok'  # cached
    assert mock_api.call_count == 2  # one installation lookup + one mint total
    assert 'orgs/openshift/installation' in fake.calls[0][0]
    assert fake.calls[1] == (f'{github_app.API_ROOT}/app/installations/424242/access_tokens', 'POST')


def test_get_bot_token_refreshes_near_expiry(bot_env):
    # Token that expires within the 300s refresh margin
    fake = _fake_api_factory(expires_in_s=100)
    with patch.object(github_app, '_api', side_effect=fake) as mock_api:
        github_app.get_bot_token()
        github_app.get_bot_token()  # margin breached -> re-mint
    assert mock_api.call_count == 4


def test_get_bot_token_unconfigured_returns_none(monkeypatch):
    monkeypatch.delenv('GITHUB_APP_ID', raising=False)
    assert github_app.get_bot_token() is None


def test_get_bot_token_failure_returns_none(bot_env):
    with patch.object(github_app, '_api', side_effect=OSError("api down")):
        assert github_app.get_bot_token() is None


# ========== endpoint wiring ==========

def test_search_uses_bot_token_when_no_session(client, bot_env):
    with patch.object(github_app, 'get_bot_token', return_value='ghs_bot_tok'), \
         patch('pr_ci_dashboard.server.search_prs', return_value={"prs": [], "total": 0}) as mock_search:
        client.post('/api/search', json={"query": "is:pr"})
    assert mock_search.call_args[1]['token'] == 'ghs_bot_tok'


def test_retest_uses_bot_token_when_no_session(client, bot_env):
    with patch.object(github_app, 'get_bot_token', return_value='ghs_bot_tok'), \
         patch('pr_ci_dashboard.server.retest_jobs', return_value={"success": True}) as mock_retest:
        client.post('/api/retest', json={
            "owner": "openshift", "repo": "origin", "pr": 1,
            "jobs": ["e2e-aws"], "type": "e2e"})
    assert mock_retest.call_args[1]['token'] == 'ghs_bot_tok'


def test_github_gate_relaxed_in_bot_mode(client, bot_env, monkeypatch):
    """DASHBOARD_REQUIRE_GITHUB is satisfied by a mounted App key: no 401,
    bot token used instead."""
    monkeypatch.setenv('DASHBOARD_REQUIRE_GITHUB', '1')
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')

    with patch.object(github_app, 'get_bot_token', return_value='ghs_bot_tok'), \
         patch('pr_ci_dashboard.server.search_prs', return_value={"prs": [], "total": 0}):
        assert client.post('/api/search', json={"query": "is:pr"}).status_code == 200

    status = client.get('/api/github/oauth/status').get_json()
    assert status['login_required'] is False  # gate satisfied by bot mode


def test_github_gate_still_active_without_key(client, monkeypatch):
    monkeypatch.setenv('DASHBOARD_REQUIRE_GITHUB', '1')
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')
    monkeypatch.delenv('GITHUB_APP_ID', raising=False)
    assert client.post('/api/search', json={"query": "is:pr"}).status_code == 401


def test_session_token_beats_bot_token(client, bot_env, monkeypatch):
    monkeypatch.setenv('GITHUB_OAUTH_CLIENT_ID', 'cid')
    from pr_ci_dashboard.utils import github_oauth
    with patch.object(github_oauth, 'start_device_flow', return_value={
        'device_code': 'dc', 'user_code': 'X', 'verification_uri': 'u',
        'interval': 5, 'expires_in': 900}):
        client.post('/api/github/oauth/start')
    with patch.object(github_oauth, 'poll_device_flow',
                      return_value={'status': 'success', 'token': 'gho_user_tok'}), \
         patch.object(github_oauth, 'get_github_login', return_value='someuser'):
        client.post('/api/github/oauth/poll')

    with patch.object(github_app, 'get_bot_token', return_value='ghs_bot_tok'), \
         patch('pr_ci_dashboard.server.search_prs', return_value={"prs": [], "total": 0}) as mock_search:
        client.post('/api/search', json={"query": "is:pr"})
    assert mock_search.call_args[1]['token'] == 'gho_user_tok'
