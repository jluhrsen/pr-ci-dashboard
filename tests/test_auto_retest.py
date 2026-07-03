"""Tests for server-side auto-retest state (DB helpers and API endpoints)."""
import pytest
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils.db import init_db, get_auto_retest_state, set_auto_retest_state


@pytest.fixture
def db_path(tmp_path):
    """Create temporary database"""
    path = tmp_path / "test.db"
    init_db(str(path))
    return str(path)


@pytest.fixture
def client(db_path):
    """Create test client with temporary database"""
    app.config['TESTING'] = True
    app.config['CSRF_ENABLED'] = False
    app.config['DB_PATH'] = db_path

    with app.test_client() as client:
        yield client


# ========== DB helper tests ==========

def test_get_auto_retest_state_empty(db_path):
    """Fresh database has no auto-retest state."""
    assert get_auto_retest_state(db_path=db_path) == {}


def test_set_and_get_auto_retest_state(db_path):
    """Set state for PRs and read it back."""
    set_auto_retest_state("openshift/ovn-kubernetes/1234", True, db_path=db_path)
    set_auto_retest_state("openshift/origin/99", False, db_path=db_path)

    state = get_auto_retest_state(db_path=db_path)
    assert state == {
        "openshift/ovn-kubernetes/1234": True,
        "openshift/origin/99": False,
    }


def test_set_auto_retest_state_upsert(db_path):
    """Setting the same pr_key again replaces the previous value."""
    set_auto_retest_state("openshift/origin/99", True, db_path=db_path)
    set_auto_retest_state("openshift/origin/99", False, db_path=db_path)

    state = get_auto_retest_state(db_path=db_path)
    assert state == {"openshift/origin/99": False}


# ========== API tests ==========

def test_api_get_empty(client):
    """GET returns empty object with no stored state."""
    response = client.get('/api/auto-retest')
    assert response.status_code == 200
    assert response.get_json() == {}


def test_api_set_then_get(client):
    """POST stores state; GET returns it."""
    response = client.post('/api/auto-retest', json={
        "pr_key": "openshift/ovn-kubernetes/1234",
        "enabled": True
    })
    assert response.status_code == 200
    assert response.get_json() == {"success": True}

    response = client.get('/api/auto-retest')
    assert response.get_json() == {"openshift/ovn-kubernetes/1234": True}


def test_api_set_disable(client):
    """POST with enabled=false persists the disabled state."""
    client.post('/api/auto-retest', json={"pr_key": "o/r/1", "enabled": True})
    client.post('/api/auto-retest', json={"pr_key": "o/r/1", "enabled": False})

    response = client.get('/api/auto-retest')
    assert response.get_json() == {"o/r/1": False}


def test_api_set_missing_fields(client):
    """POST without required fields returns 400."""
    assert client.post('/api/auto-retest', json={}).status_code == 400
    assert client.post('/api/auto-retest', json={"pr_key": "o/r/1"}).status_code == 400
    assert client.post('/api/auto-retest', json={"enabled": True}).status_code == 400


def test_api_set_invalid_types(client):
    """POST with wrong field types returns 400."""
    response = client.post('/api/auto-retest', json={"pr_key": 123, "enabled": True})
    assert response.status_code == 400
    response = client.post('/api/auto-retest', json={"pr_key": "o/r/1", "enabled": "yes"})
    assert response.status_code == 400


def test_api_set_invalid_pr_key_format(client):
    """POST with malformed pr_key returns 400."""
    for bad_key in ["not-a-key", "owner/repo", "owner/repo/notanumber", "//1", "a/b/1/2"]:
        response = client.post('/api/auto-retest', json={"pr_key": bad_key, "enabled": True})
        assert response.status_code == 400, f"expected 400 for pr_key={bad_key!r}"


def test_api_set_invalid_json(client):
    """POST with non-JSON body returns 400."""
    response = client.post('/api/auto-retest', data="not json",
                           content_type='application/json')
    assert response.status_code == 400
