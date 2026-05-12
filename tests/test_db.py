import pytest
import sqlite3
import os
from utils.db import init_db

def test_init_db_creates_tables(tmp_path):
    """Test that init_db creates job_analyses table with correct schema"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

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

    conn.close()
