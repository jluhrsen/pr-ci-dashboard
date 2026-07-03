"""Tests for per-user Google sign-in (web flow module, endpoints, analysis ADC pass-through)."""
import base64
import json
import os
import pytest
from unittest.mock import patch, MagicMock
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils.session_store import GOOGLE_SESSIONS
from pr_ci_dashboard.utils import google_oauth
from pr_ci_dashboard.utils.db import init_db


@pytest.fixture
def client(tmp_path):
    """Test client with temp DB and clean session state."""
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
def oauth_env(monkeypatch):
    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_ID', 'cid.apps.googleusercontent.com')
    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_SECRET', 'shhh')


def _fake_id_token(email, iss='https://accounts.google.com', aud='cid.apps.googleusercontent.com', exp_offset=3600):
    import time
    claims = {'email': email, 'iss': iss, 'aud': aud, 'exp': time.time() + exp_offset}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return f'header.{payload}.signature'


# ========== google_oauth module ==========

def test_get_client_config(monkeypatch):
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_ID', raising=False)
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_SECRET', raising=False)
    assert google_oauth.get_client_config() is None

    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_ID', 'cid')
    assert google_oauth.get_client_config() is None  # secret still missing

    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_SECRET', 'sec')
    assert google_oauth.get_client_config() == ('cid', 'sec')


def test_build_auth_url_params():
    url = google_oauth.build_auth_url('cid', 'http://localhost:5000/cb', 'st4te', 'ch4llenge')
    assert url.startswith(google_oauth.AUTH_URL + '?')
    for fragment in ('client_id=cid', 'state=st4te', 'code_challenge=ch4llenge',
                     'code_challenge_method=S256', 'access_type=offline',
                     'prompt=consent', 'response_type=code'):
        assert fragment in url
    assert 'cloud-platform' in url


def test_make_pkce_pair():
    verifier, challenge = google_oauth.make_pkce_pair()
    import hashlib
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    assert challenge == expected


def test_exchange_code_requires_refresh_token():
    with patch.object(google_oauth, '_post_form', return_value={'access_token': 'at'}):
        with pytest.raises(RuntimeError):
            google_oauth.exchange_code('cid', 'sec', 'code', 'uri', 'ver')

    with patch.object(google_oauth, '_post_form', return_value={
        'access_token': 'at', 'refresh_token': 'rt', 'id_token': 'idt'
    }):
        tokens = google_oauth.exchange_code('cid', 'sec', 'code', 'uri', 'ver')
    assert tokens['refresh_token'] == 'rt'


def test_email_from_id_token():
    assert google_oauth.email_from_id_token(_fake_id_token('a@redhat.com')) == 'a@redhat.com'
    assert google_oauth.email_from_id_token('garbage') is None


def test_email_from_id_token_claim_checks():
    """Defense-in-depth claim validation: iss, aud (when given), exp."""
    good = _fake_id_token('a@redhat.com')
    assert google_oauth.email_from_id_token(good, client_id='cid.apps.googleusercontent.com') == 'a@redhat.com'

    # Wrong audience rejected when client_id given
    assert google_oauth.email_from_id_token(good, client_id='other-client') is None
    # Wrong issuer rejected
    assert google_oauth.email_from_id_token(_fake_id_token('a@redhat.com', iss='https://evil.example')) is None
    # Expired token rejected
    assert google_oauth.email_from_id_token(_fake_id_token('a@redhat.com', exp_offset=-60)) is None


def test_build_adc_shape():
    adc = google_oauth.build_adc('cid', 'sec', 'rt')
    assert adc == {
        'type': 'authorized_user', 'client_id': 'cid',
        'client_secret': 'sec', 'refresh_token': 'rt'
    }


# ========== API endpoints ==========

def test_status_disabled(client, monkeypatch):
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_ID', raising=False)
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_SECRET', raising=False)
    status = client.get('/api/google/oauth/status').get_json()
    assert status == {"enabled": False, "connected": False, "email": None}


def test_login_requires_config(client, monkeypatch):
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_ID', raising=False)
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_SECRET', raising=False)
    assert client.get('/api/google/oauth/login').status_code == 400


def test_login_redirects_to_google(client, oauth_env):
    response = client.get('/api/google/oauth/login')
    assert response.status_code == 302
    assert response.location.startswith(google_oauth.AUTH_URL)
    assert 'code_challenge=' in response.location
    # State stored in the session for callback verification
    with client.session_transaction() as sess:
        assert sess['google_oauth_state']
        assert sess['google_oauth_verifier']


def test_callback_rejects_bad_state(client, oauth_env):
    client.get('/api/google/oauth/login')
    response = client.get('/api/google/oauth/callback?code=abc&state=WRONG')
    assert response.status_code == 400


def test_callback_without_login_rejected(client, oauth_env):
    assert client.get('/api/google/oauth/callback?code=abc&state=x').status_code == 400


def test_callback_success_stores_session(client, oauth_env):
    client.get('/api/google/oauth/login')
    with client.session_transaction() as sess:
        state = sess['google_oauth_state']

    with patch.object(google_oauth, 'exchange_code', return_value={
        'refresh_token': 'rt', 'access_token': 'at',
        'id_token': _fake_id_token('jluhrsen@redhat.com')
    }):
        response = client.get(f'/api/google/oauth/callback?code=abc&state={state}')
    assert response.status_code == 302
    assert response.location.endswith('/')

    status = client.get('/api/google/oauth/status').get_json()
    assert status == {"enabled": True, "connected": True, "email": "jluhrsen@redhat.com"}

    # State/verifier are single-use: replaying the callback fails
    assert client.get(f'/api/google/oauth/callback?code=abc&state={state}').status_code == 400

    # Disconnect drops the session
    client.post('/api/google/oauth/disconnect')
    assert client.get('/api/google/oauth/status').get_json()['connected'] is False


def test_callback_denied_redirects(client, oauth_env):
    client.get('/api/google/oauth/login')
    response = client.get('/api/google/oauth/callback?error=access_denied')
    assert response.status_code == 302
    assert 'google_auth=denied' in response.location


# ========== analysis ADC pass-through ==========

def _sign_in(client):
    client.get('/api/google/oauth/login')
    with client.session_transaction() as sess:
        state = sess['google_oauth_state']
    with patch.object(google_oauth, 'exchange_code', return_value={
        'refresh_token': 'rt', 'access_token': 'at',
        'id_token': _fake_id_token('user@redhat.com')
    }):
        client.get(f'/api/google/oauth/callback?code=abc&state={state}')


_PROW = 'https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs'
ANALYZE_REQUEST = {
    "pr": "openshift/origin#1", "repo": "openshift/origin",
    "job_name": "e2e-aws",
    "job_urls": [f"{_PROW}/1", f"{_PROW}/2", f"{_PROW}/3"]
}


def test_analysis_passes_user_adc(client, oauth_env):
    """Signed-in user's ADC dict reaches analyze_permafail."""
    _sign_in(client)

    with patch('pr_ci_dashboard.api.analysis.analyze_permafail',
               return_value={"permafail": False, "reason": "flake"}) as mock_analyze:
        client.post('/api/jobs/analyze', json=ANALYZE_REQUEST)

    adc = mock_analyze.call_args[1]['google_adc']
    assert adc['type'] == 'authorized_user'
    assert adc['refresh_token'] == 'rt'


def test_analysis_without_signin_uses_fallback(client):
    """No Google session -> google_adc is None (pod credentials)."""
    with patch('pr_ci_dashboard.api.analysis.analyze_permafail',
               return_value={"permafail": False, "reason": "flake"}) as mock_analyze:
        client.post('/api/jobs/analyze', json=ANALYZE_REQUEST)

    assert mock_analyze.call_args[1]['google_adc'] is None


# ========== transient ADC file handling ==========

def test_build_claude_env_writes_and_cleans_adc():
    from pr_ci_dashboard.utils.ai_analyzer import build_claude_env, cleanup_adc

    adc = {'type': 'authorized_user', 'refresh_token': 'rt',
           'client_id': 'cid', 'client_secret': 'sec'}
    env, adc_path = build_claude_env(adc)

    assert env['GOOGLE_APPLICATION_CREDENTIALS'] == adc_path
    with open(adc_path) as f:
        assert json.load(f) == adc

    cleanup_adc(adc_path)
    assert not os.path.exists(adc_path)
    cleanup_adc(adc_path)  # idempotent
    cleanup_adc(None)      # no-op


def test_build_claude_env_without_adc():
    from pr_ci_dashboard.utils.ai_analyzer import build_claude_env

    env, adc_path = build_claude_env(None)
    assert adc_path is None
    # Pod-level credentials untouched
    assert env.get('GOOGLE_APPLICATION_CREDENTIALS') == os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')


def test_analyze_permafail_cleans_adc_file():
    """The transient credentials file is deleted after the subprocess runs."""
    from pr_ci_dashboard.utils import ai_analyzer

    adc = {'type': 'authorized_user', 'refresh_token': 'rt'}
    seen = {}

    def fake_run(cmd, **kwargs):
        seen['adc_path'] = kwargs['env']['GOOGLE_APPLICATION_CREDENTIALS']
        seen['existed'] = os.path.exists(seen['adc_path'])
        return MagicMock(returncode=0, stdout='{"permafail": false, "reason": "x"}', stderr='')

    with patch.object(ai_analyzer.subprocess, 'run', side_effect=fake_run):
        result = ai_analyzer.analyze_permafail(['u1', 'u2'], 'job', 'org/repo#1', google_adc=adc)

    assert result == {"permafail": False, "reason": "x"}
    assert seen['existed'] is True
    assert not os.path.exists(seen['adc_path'])


def test_streaming_generator_close_kills_child_and_cleans_adc():
    """Abandoning the SSE stream (client disconnect) terminates the claude
    subprocess and removes the transient ADC file."""
    import subprocess as real_subprocess
    from pr_ci_dashboard.utils import ai_analyzer

    adc = {'type': 'authorized_user', 'refresh_token': 'rt'}
    spawned = {}
    # Capture before patching: ai_analyzer.subprocess IS this module, so the
    # fake must not call the patched attribute (infinite recursion)
    orig_popen = real_subprocess.Popen

    def fake_popen(cmd, **kwargs):
        # Long-running child that emits a line immediately so the generator
        # yields quickly after startup
        proc = orig_popen(
            ['bash', '-c', 'echo started; sleep 60'],
            stdin=kwargs.get('stdin'), stdout=kwargs.get('stdout'),
            stderr=kwargs.get('stderr'), text=kwargs.get('text'),
            env=kwargs.get('env'))
        spawned['proc'] = proc
        spawned['adc_path'] = kwargs['env']['GOOGLE_APPLICATION_CREDENTIALS']
        return proc

    with patch.object(ai_analyzer.subprocess, 'Popen', side_effect=fake_popen):
        gen = ai_analyzer.analyze_permafail_streaming(['u1', 'u2'], 'job', 'org/repo#1', google_adc=adc)
        # 4 pre-spawn status events, then the first line from the child
        for _ in range(5):
            next(gen)
        assert spawned['proc'].poll() is None  # child running
        assert os.path.exists(spawned['adc_path'])

        gen.close()  # simulates client disconnect

    assert spawned['proc'].poll() is not None  # child terminated
    assert not os.path.exists(spawned['adc_path'])  # ADC file removed
