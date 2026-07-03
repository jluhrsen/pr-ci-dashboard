"""Tests for the /healthz probe endpoint."""
import pytest
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils.db import init_db


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    app.config['TESTING'] = True
    app.config['CSRF_ENABLED'] = False
    app.config['DB_PATH'] = str(db_path)
    with app.test_client() as client:
        yield client


def test_healthz_ok(client):
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_healthz_unhealthy_on_bad_db(client, tmp_path):
    app.config['DB_PATH'] = str(tmp_path / "missing-dir" / "no.db")
    response = client.get('/healthz')
    assert response.status_code == 503
    assert response.get_json()['status'] == 'unhealthy'


def test_healthz_not_login_gated(client, monkeypatch):
    monkeypatch.setenv('DASHBOARD_REQUIRE_LOGIN', '1')
    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_ID', 'cid')
    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_SECRET', 'sec')
    assert client.get('/healthz').status_code == 200


def test_healthz_missing_db_file_not_created(client, tmp_path):
    """A missing DB file (with existing parent dir) is unhealthy - and the
    probe must not create it as a side effect (sqlite default connect would)."""
    import os
    missing = tmp_path / "never-created.db"
    app.config['DB_PATH'] = str(missing)

    response = client.get('/healthz')
    assert response.status_code == 503
    assert not os.path.exists(missing)


def test_healthz_incomplete_schema(client, tmp_path):
    """A reachable DB without the expected tables is unhealthy."""
    import sqlite3
    empty = tmp_path / "empty.db"
    sqlite3.connect(str(empty)).close()  # creates a schemaless DB
    app.config['DB_PATH'] = str(empty)

    response = client.get('/healthz')
    assert response.status_code == 503
    assert 'schema' in response.get_json()['error']
