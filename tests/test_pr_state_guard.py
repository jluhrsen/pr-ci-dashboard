"""Tests for PR state checks: get_pr_state() and /api/retest guard."""
import pytest
from unittest.mock import patch, MagicMock
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils.job_executor import get_pr_state
from pr_ci_dashboard.utils.db import init_db
from pr_ci_dashboard.utils import rate_limit


RETEST_BODY = {"owner": "openshift", "repo": "origin", "pr": 1,
               "jobs": ["e2e-aws"], "type": "e2e"}


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


# ========== get_pr_state unit tests ==========

class TestGetPrState:
    def test_open_pr(self):
        result = MagicMock(returncode=0, stdout="OPEN\n")
        with patch('pr_ci_dashboard.utils.job_executor.subprocess.run',
                   return_value=result):
            assert get_pr_state("openshift/origin", 1) == {"state": "OPEN"}

    def test_merged_pr(self):
        result = MagicMock(returncode=0, stdout="MERGED\n")
        with patch('pr_ci_dashboard.utils.job_executor.subprocess.run',
                   return_value=result):
            assert get_pr_state("openshift/origin", 1) == {"state": "MERGED"}

    def test_closed_pr(self):
        result = MagicMock(returncode=0, stdout="CLOSED\n")
        with patch('pr_ci_dashboard.utils.job_executor.subprocess.run',
                   return_value=result):
            assert get_pr_state("openshift/origin", 1) == {"state": "CLOSED"}

    def test_subprocess_failure_returns_unknown(self):
        result = MagicMock(returncode=1, stdout="", stderr="not found")
        with patch('pr_ci_dashboard.utils.job_executor.subprocess.run',
                   return_value=result):
            assert get_pr_state("openshift/origin", 1) == {"state": "UNKNOWN"}

    def test_timeout_returns_unknown(self):
        import subprocess
        with patch('pr_ci_dashboard.utils.job_executor.subprocess.run',
                   side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=5)):
            assert get_pr_state("openshift/origin", 1) == {"state": "UNKNOWN"}

    def test_unexpected_output_returns_unknown(self):
        result = MagicMock(returncode=0, stdout="DRAFT\n")
        with patch('pr_ci_dashboard.utils.job_executor.subprocess.run',
                   return_value=result):
            assert get_pr_state("openshift/origin", 1) == {"state": "UNKNOWN"}

    def test_token_passed_to_env(self):
        result = MagicMock(returncode=0, stdout="OPEN\n")
        with patch('pr_ci_dashboard.utils.job_executor.subprocess.run',
                   return_value=result) as mock_run:
            get_pr_state("openshift/origin", 1, token="gho_abc")
            env = mock_run.call_args[1]['env']
            assert env['GH_TOKEN'] == 'gho_abc'


# ========== /api/retest PR state guard tests ==========

class TestRetestPrStateGuard:
    def test_retest_blocked_for_merged_pr(self, client):
        with patch('pr_ci_dashboard.server.get_pr_state',
                   return_value={"state": "MERGED"}):
            response = client.post('/api/retest', json=RETEST_BODY)
        assert response.status_code == 409
        assert "merged" in response.get_json()["error"]

    def test_retest_blocked_for_closed_pr(self, client):
        with patch('pr_ci_dashboard.server.get_pr_state',
                   return_value={"state": "CLOSED"}):
            response = client.post('/api/retest', json=RETEST_BODY)
        assert response.status_code == 409
        assert "closed" in response.get_json()["error"]

    def test_retest_blocked_when_state_unknown(self, client):
        with patch('pr_ci_dashboard.server.get_pr_state',
                   return_value={"state": "UNKNOWN"}):
            response = client.post('/api/retest', json=RETEST_BODY)
        assert response.status_code == 502
        assert "Could not verify" in response.get_json()["error"]

    def test_retest_allowed_for_open_pr(self, client):
        with patch('pr_ci_dashboard.server.get_pr_state',
                   return_value={"state": "OPEN"}) as mock_state, \
             patch('pr_ci_dashboard.server.retest_jobs',
                   return_value={"success": True}):
            response = client.post('/api/retest', json=RETEST_BODY)
        assert response.status_code == 200
        mock_state.assert_called_once_with(
            "openshift/origin", 1, token=None
        )

    def test_get_pr_state_receives_correct_args(self, client):
        with patch('pr_ci_dashboard.server.get_pr_state',
                   return_value={"state": "OPEN"}) as mock_state, \
             patch('pr_ci_dashboard.server.retest_jobs',
                   return_value={"success": True}):
            client.post('/api/retest', json={
                "owner": "openshift", "repo": "ovn-kubernetes",
                "pr": 3298, "jobs": ["e2e-aws"], "type": "e2e"})
        args, kwargs = mock_state.call_args
        assert args[0] == "openshift/ovn-kubernetes"
        assert args[1] == 3298

    def test_no_comment_posted_for_merged_pr(self, client):
        with patch('pr_ci_dashboard.server.get_pr_state',
                   return_value={"state": "MERGED"}), \
             patch('pr_ci_dashboard.server.retest_jobs') as mock_retest:
            client.post('/api/retest', json=RETEST_BODY)
        mock_retest.assert_not_called()


class TestPerJobCooldown:
    def test_duplicate_retest_blocked(self, client):
        """Second retest of the same job within 5 minutes is rejected."""
        with patch('pr_ci_dashboard.server.get_pr_state',
                   return_value={"state": "OPEN"}), \
             patch('pr_ci_dashboard.server.retest_jobs',
                   return_value={"success": True}):
            r1 = client.post('/api/retest', json=RETEST_BODY)
            r2 = client.post('/api/retest', json=RETEST_BODY)
        assert r1.status_code == 200
        assert r2.status_code == 429
        assert "retested recently" in r2.get_json()["error"]

    def test_different_jobs_not_blocked(self, client):
        """Different jobs on the same PR are independent."""
        body2 = {**RETEST_BODY, "jobs": ["e2e-gcp"]}
        with patch('pr_ci_dashboard.server.get_pr_state',
                   return_value={"state": "OPEN"}), \
             patch('pr_ci_dashboard.server.retest_jobs',
                   return_value={"success": True}):
            r1 = client.post('/api/retest', json=RETEST_BODY)
            r2 = client.post('/api/retest', json=body2)
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_partial_cooldown_filters_jobs(self, client):
        """When some jobs are cooled and others are not, only fresh jobs post."""
        with patch('pr_ci_dashboard.server.get_pr_state',
                   return_value={"state": "OPEN"}), \
             patch('pr_ci_dashboard.server.retest_jobs',
                   return_value={"success": True}) as mock_retest:
            client.post('/api/retest', json=RETEST_BODY)
            multi = {**RETEST_BODY, "jobs": ["e2e-aws", "e2e-gcp"]}
            r2 = client.post('/api/retest', json=multi)
        assert r2.status_code == 200
        second_call_jobs = mock_retest.call_args_list[-1][0][3]
        assert second_call_jobs == ["e2e-gcp"]
