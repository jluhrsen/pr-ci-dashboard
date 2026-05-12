import pytest
import sqlite3
import os
from utils.db import init_db

def test_init_db_creates_tables(tmp_path):
    """Test that init_db creates job_analyses table with correct schema"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Check table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_analyses'")
        assert cursor.fetchone() is not None

        # Check schema
        cursor.execute("PRAGMA table_info(job_analyses)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}

        assert columns['job_url'] == 'TEXT'
        assert columns['pr_number'] == 'INTEGER'
        assert columns['repo'] == 'TEXT'
        assert columns['job_name'] == 'TEXT'
        assert columns['signature'] == 'TEXT'
        assert columns['analyzed_at'] == 'TIMESTAMP'
        assert columns['permafail_result'] == 'TEXT'
        assert columns['override'] == 'BOOLEAN'
    finally:
        if conn:
            conn.close()


def test_init_db_idempotent(tmp_path):
    """Test that init_db can be called multiple times without error"""
    db_path = tmp_path / "test.db"

    # Call init_db multiple times
    init_db(str(db_path))
    init_db(str(db_path))
    init_db(str(db_path))

    # Verify table still exists and is intact
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_analyses'")
        assert cursor.fetchone() is not None
    finally:
        if conn:
            conn.close()


def test_init_db_with_existing_database(tmp_path):
    """Test that init_db works with an existing database"""
    db_path = tmp_path / "test.db"

    # Create initial database
    init_db(str(db_path))

    # Insert some test data
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO job_analyses
            (job_url, pr_number, repo, job_name, signature, permafail_result, override)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("http://example.com", 123, "test-repo", "test-job", "sig123", "permafail", 0))
        conn.commit()
    finally:
        if conn:
            conn.close()

    # Call init_db again
    init_db(str(db_path))

    # Verify data is still there
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM job_analyses")
        count = cursor.fetchone()[0]
        assert count == 1
    finally:
        if conn:
            conn.close()


def test_init_db_override_check_constraint(tmp_path):
    """Test that override column has CHECK constraint for boolean values"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Valid values should work
        cursor.execute("""
            INSERT INTO job_analyses
            (job_url, pr_number, repo, job_name, signature, permafail_result, override)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("http://example1.com", 1, "repo1", "job1", "sig1", "permafail", 0))

        cursor.execute("""
            INSERT INTO job_analyses
            (job_url, pr_number, repo, job_name, signature, permafail_result, override)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("http://example2.com", 2, "repo2", "job2", "sig2", "permafail", 1))

        conn.commit()

        # Invalid value should fail
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute("""
                INSERT INTO job_analyses
                (job_url, pr_number, repo, job_name, signature, permafail_result, override)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("http://example3.com", 3, "repo3", "job3", "sig3", "permafail", 2))
            conn.commit()
    finally:
        if conn:
            conn.close()


def test_init_db_invalid_path():
    """Test that init_db raises RuntimeError for invalid database paths"""
    # Try to create database in non-existent directory
    invalid_path = "/nonexistent/directory/that/does/not/exist/database.db"

    with pytest.raises(RuntimeError, match="Failed to initialize database"):
        init_db(invalid_path)
