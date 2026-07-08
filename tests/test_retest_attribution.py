"""Tests for requester attribution in retest comments."""
import pytest
from unittest.mock import patch
from pr_ci_dashboard.server import app
from pr_ci_dashboard.api.retest import retest_jobs
from pr_ci_dashboard.utils.session_store import GITHUB_SESSIONS, GOOGLE_SESSIONS
from pr_ci_dashboard.utils import github_app
from pr_ci_dashboard.utils.db import init_db


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    app.config['TESTING'] = True
    app.config['CSRF_ENABLED'] = False
    app.config['DB_PATH'] = str(db_path)
    GITHUB_SESSIONS.clear()
    GOOGLE_SESSIONS.clear()
    with app.test_client() as client:
        yield client
    GITHUB_SESSIONS.clear()
    GOOGLE_SESSIONS.clear()


RETEST_BODY = {"owner": "openshift", "repo": "origin", "pr": 1,
               "jobs": ["e2e-aws"], "type": "e2e"}


# ========== comment body construction ==========

def test_comment_without_attribution():
    with patch('pr_ci_dashboard.api.retest.post_retest_comment',
               return_value={"success": True}) as mock_post:
        retest_jobs("openshift", "origin", 1, ["e2e-aws"], "e2e")
    assert mock_post.call_args[0][3] == "/test e2e-aws"


def test_comment_with_manual_attribution():
    with patch('pr_ci_dashboard.api.retest.post_retest_comment',
               return_value={"success": True}) as mock_post:
        retest_jobs("openshift", "origin", 1, ["e2e-aws", "e2e-gcp"], "e2e",
                    requested_by="jluhrsen@redhat.com")
    body = mock_post.call_args[0][3]
    assert body == ("/test e2e-aws\n/test e2e-gcp\n\n"
                    "👻🚫 retest requested by `jluhrsen@redhat.com` via Flake Buster")


def test_comment_with_auto_attribution_payload():
    with patch('pr_ci_dashboard.api.retest.post_retest_comment',
               return_value={"success": True}) as mock_post:
        retest_jobs("openshift", "origin", 1, ["4.22-e2e-aws"], "payload",
                    requested_by="jluhrsen@redhat.com", auto=True)
    body = mock_post.call_args[0][3]
    assert body.startswith("/payload-job 4.22-e2e-aws\n\n")
    assert "auto-retest triggered for `jluhrsen@redhat.com`" in body


# ========== endpoint wiring ==========

def test_bot_post_attributes_google_user(client, monkeypatch):
    """Bot posts + Google-signed-in human -> attribution with their email."""
    from pr_ci_dashboard.utils.session_store import session_id
    with client.session_transaction() as sess:
        sess['sid'] = 'test-sid'
    import time
    GOOGLE_SESSIONS['test-sid'] = {"adc": {}, "email": "jluhrsen@redhat.com",
                                   "last_seen": time.time()}

    with patch.object(github_app, 'get_bot_token', return_value='ghs_bot'), \
         patch('pr_ci_dashboard.server.retest_jobs',
               return_value={"success": True}) as mock_retest:
        client.post('/api/retest', json={**RETEST_BODY, "auto": True})

    kwargs = mock_retest.call_args[1]
    assert kwargs['requested_by'] == 'jluhrsen@redhat.com'
    assert kwargs['auto'] is True


def test_session_post_has_no_attribution(client, monkeypatch):
    """A user posting with their own connected token needs no attribution."""
    import time
    with client.session_transaction() as sess:
        sess['sid'] = 'test-sid'
    GITHUB_SESSIONS['test-sid'] = {"token": "gho_user", "login": "jluhrsen",
                                   "last_seen": time.time()}

    with patch('pr_ci_dashboard.server.retest_jobs',
               return_value={"success": True}) as mock_retest:
        client.post('/api/retest', json=RETEST_BODY)

    assert mock_retest.call_args[1]['requested_by'] is None


def test_bot_fallback_retry_attributes(client, monkeypatch, tmp_path):
    """Org-blocked user token -> bot retry carries the attribution."""
    import time
    monkeypatch.setenv('GITHUB_APP_ID', '1460951')
    key = tmp_path / "k.pem"
    key.write_text("dummy")
    monkeypatch.setenv('GITHUB_APP_PRIVATE_KEY_FILE', str(key))
    with client.session_transaction() as sess:
        sess['sid'] = 'test-sid'
    GITHUB_SESSIONS['test-sid'] = {"token": "gho_user", "login": "jluhrsen",
                                   "last_seen": time.time()}

    org_error = {"error": "the `openshift` organization has enabled OAuth App access restrictions"}

    def fake_retest(owner, repo, pr, jobs, job_type, token=None, requested_by=None, auto=False):
        return {"success": True} if token == 'ghs_bot' else org_error

    with patch.object(github_app, 'get_bot_token', return_value='ghs_bot'), \
         patch('pr_ci_dashboard.server.retest_jobs', side_effect=fake_retest) as mock_retest:
        response = client.post('/api/retest', json=RETEST_BODY)

    assert response.get_json() == {"success": True}
    first, second = mock_retest.call_args_list
    assert first[1]['requested_by'] is None          # user posting as self
    assert second[1]['requested_by'] == 'jluhrsen'   # bot posting for them
