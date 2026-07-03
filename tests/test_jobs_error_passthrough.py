"""Backend script errors must reach the API response (the UI renders them)."""
import pytest
from unittest.mock import patch
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils.db import init_db


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    app.config['TESTING'] = True
    app.config['DB_PATH'] = str(db_path)
    with app.test_client() as client:
        yield client


def test_job_fetch_error_passes_through_api(client):
    """A failing job script yields an error field in the /api/pr response,
    not a silent empty result."""
    error_result = {"error": "Script failed", "stderr": "jq: command not found",
                    "failed": [], "running": []}
    ok_result = {"failed": [], "running": []}

    with patch('pr_ci_dashboard.api.jobs.get_e2e_jobs', return_value=error_result), \
         patch('pr_ci_dashboard.api.jobs.get_payload_jobs', return_value=ok_result):
        data = client.get('/api/pr/openshift/origin/1').get_json()

    assert data['e2e']['error'] == "Script failed"
    assert data['e2e']['stderr'] == "jq: command not found"
    assert 'error' not in data['payload']
