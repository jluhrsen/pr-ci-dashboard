"""Tests for server-side auto-retest state (DB helpers and API endpoints)."""
import sqlite3

import pytest
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils.db import (DEFAULT_FAILURE_THRESHOLD, init_db, get_auto_retest_state,
                                      set_auto_retest_state, get_audit_log)


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
    """Set state for PRs and read it back, with AI analysis off by default."""
    set_auto_retest_state("openshift/ovn-kubernetes/1234", True, db_path=db_path)
    set_auto_retest_state("openshift/origin/99", False, db_path=db_path)

    state = get_auto_retest_state(db_path=db_path)
    assert state == {
        "openshift/ovn-kubernetes/1234": {
            "enabled": True,
            "ai_enabled": False,
            "failure_threshold": DEFAULT_FAILURE_THRESHOLD,
        },
        "openshift/origin/99": {
            "enabled": False,
            "ai_enabled": False,
            "failure_threshold": DEFAULT_FAILURE_THRESHOLD,
        },
    }


def test_set_auto_retest_state_upsert(db_path):
    """Setting the same pr_key again replaces the previous value."""
    set_auto_retest_state("openshift/origin/99", True, db_path=db_path)
    set_auto_retest_state("openshift/origin/99", False, db_path=db_path)

    state = get_auto_retest_state(db_path=db_path)
    assert state["openshift/origin/99"]["enabled"] is False


def test_set_ai_enabled_and_threshold(db_path):
    """AI analysis and failure threshold persist per PR."""
    set_auto_retest_state("openshift/origin/99", True, ai_enabled=True,
                          failure_threshold=5, db_path=db_path)

    state = get_auto_retest_state(db_path=db_path)
    assert state == {
        "openshift/origin/99": {"enabled": True, "ai_enabled": True, "failure_threshold": 5},
    }


def test_set_auto_retest_state_preserves_unset_fields(db_path):
    """Toggling auto-retest keeps the PR's AI setting and threshold."""
    set_auto_retest_state("openshift/origin/99", True, ai_enabled=True,
                          failure_threshold=5, db_path=db_path)
    set_auto_retest_state("openshift/origin/99", False, db_path=db_path)

    state = get_auto_retest_state(db_path=db_path)
    assert state == {
        "openshift/origin/99": {"enabled": False, "ai_enabled": True, "failure_threshold": 5},
    }


def test_init_db_migrates_legacy_auto_retest_table(tmp_path):
    """A pre-existing table without the new columns is migrated in place."""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE auto_retest (
            pr_key TEXT PRIMARY KEY,
            enabled BOOLEAN NOT NULL CHECK (enabled IN (0, 1)),
            updated_at TIMESTAMP
        )
    """)
    conn.execute("INSERT INTO auto_retest VALUES ('openshift/origin/99', 1, '2026-01-01T00:00:00')")
    conn.commit()
    conn.close()

    init_db(path)

    state = get_auto_retest_state(db_path=path)
    assert state == {
        "openshift/origin/99": {
            "enabled": True,
            "ai_enabled": False,
            "failure_threshold": DEFAULT_FAILURE_THRESHOLD,
        },
    }


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
    assert response.get_json() == {
        "openshift/ovn-kubernetes/1234": {
            "enabled": True,
            "ai_enabled": False,
            "failure_threshold": DEFAULT_FAILURE_THRESHOLD,
        },
    }


def test_api_set_disable(client):
    """POST with enabled=false persists the disabled state."""
    client.post('/api/auto-retest', json={"pr_key": "o/r/1", "enabled": True})
    client.post('/api/auto-retest', json={"pr_key": "o/r/1", "enabled": False})

    response = client.get('/api/auto-retest')
    assert response.get_json()["o/r/1"]["enabled"] is False


def test_api_set_ai_enabled_and_threshold(client):
    """POST stores the AI switch and failure threshold for a PR."""
    response = client.post('/api/auto-retest', json={
        "pr_key": "o/r/1",
        "enabled": True,
        "ai_enabled": True,
        "failure_threshold": 5,
    })
    assert response.status_code == 200

    response = client.get('/api/auto-retest')
    assert response.get_json() == {
        "o/r/1": {"enabled": True, "ai_enabled": True, "failure_threshold": 5},
    }


def test_api_set_invalid_ai_enabled(client):
    """POST with a non-boolean ai_enabled returns 400."""
    response = client.post('/api/auto-retest', json={
        "pr_key": "o/r/1", "enabled": True, "ai_enabled": "yes"})
    assert response.status_code == 400


def test_api_set_invalid_threshold(client):
    """POST with an out-of-range or non-integer failure_threshold returns 400."""
    for bad_threshold in [0, 11, "3", 2.5, True, None]:
        response = client.post('/api/auto-retest', json={
            "pr_key": "o/r/1", "enabled": True, "failure_threshold": bad_threshold})
        assert response.status_code == 400, f"expected 400 for failure_threshold={bad_threshold!r}"


def test_api_config_change_records_audit(client, db_path):
    """Changing the AI switch or threshold appends an audit entry."""
    client.post('/api/auto-retest', json={
        "pr_key": "o/r/1", "enabled": True, "ai_enabled": True, "failure_threshold": 5})

    entries = get_audit_log(limit=10, db_path=db_path)
    config_entries = [e for e in entries if e["action"] == "auto-retest-config"]
    assert len(config_entries) == 1
    assert config_entries[0]["target"] == "o/r/1"
    assert config_entries[0]["result"] == "ai_enabled=True; failure_threshold=5"


def test_api_config_change_while_off_records_no_disable_audit(client, db_path):
    """Changing config on a PR with auto-retest off is not a disable action."""
    client.post('/api/auto-retest', json={
        "pr_key": "o/r/1", "enabled": False, "ai_enabled": True})

    entries = get_audit_log(limit=10, db_path=db_path)
    assert [e["action"] for e in entries] == ["auto-retest-config"]


def test_api_plain_toggle_records_no_config_audit(client, db_path):
    """Enabling auto-retest without config fields does not write a config audit entry."""
    client.post('/api/auto-retest', json={"pr_key": "o/r/1", "enabled": True})

    entries = get_audit_log(limit=10, db_path=db_path)
    assert [e for e in entries if e["action"] == "auto-retest-config"] == []


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


def test_api_disable_records_audit_reason(client, db_path):
    """Disabling auto-retest with a reason appends an audit log entry."""
    client.post('/api/auto-retest', json={
        "pr_key": "openshift/ovn-kubernetes/1234",
        "enabled": True,
    })
    response = client.post('/api/auto-retest', json={
        "pr_key": "openshift/ovn-kubernetes/1234",
        "enabled": False,
        "reason": "all failing jobs are permafails",
    })
    assert response.status_code == 200

    entries = get_audit_log(limit=10, db_path=db_path)
    disable_entries = [e for e in entries if e["action"] == "auto-retest-disable"]
    assert len(disable_entries) == 1
    assert disable_entries[0]["target"] == "openshift/ovn-kubernetes/1234"
    assert disable_entries[0]["result"] == "all failing jobs are permafails"


def test_api_disable_without_reason_records_default(client, db_path):
    """Disabling without a reason still records an audit entry."""
    client.post('/api/auto-retest', json={"pr_key": "o/r/1", "enabled": False})

    entries = get_audit_log(limit=5, db_path=db_path)
    assert entries[0]["action"] == "auto-retest-disable"
    assert entries[0]["result"] == "no reason given"
