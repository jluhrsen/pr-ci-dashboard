import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'dashboard.db')

def init_db(db_path=None):
    """Initialize database and create tables if they don't exist

    Args:
        db_path: Optional path to database file. Defaults to dashboard.db in project root.

    Raises:
        RuntimeError: If database initialization fails.
    """
    path = db_path or DB_PATH
    conn = None
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS job_analyses (
                job_url TEXT PRIMARY KEY,
                pr_number INTEGER,
                repo TEXT,
                job_name TEXT,
                signature TEXT,
                analyzed_at TIMESTAMP,
                permafail_result TEXT,
                override BOOLEAN DEFAULT 0 CHECK (override IN (0, 1))
            )
        """)

        conn.commit()
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to initialize database at {path}: {e}")
    finally:
        if conn:
            conn.close()


def store_analysis(job_url, pr_number, repo, job_name, signature, permafail_result, db_path=None):
    """
    Store permafail analysis result in database

    Args:
        job_url: Prow job URL (primary key)
        pr_number: PR number
        repo: Repository (e.g., "openshift/ovn-kubernetes")
        job_name: Job name (e.g., "e2e-aws-ovn")
        signature: Failure signature dict
        permafail_result: Analysis result dict
        db_path: Optional database path (defaults to DB_PATH)

    Raises:
        RuntimeError: If database operation fails
    """
    path = db_path or DB_PATH
    conn = None
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO job_analyses
            (job_url, pr_number, repo, job_name, signature, analyzed_at, permafail_result, override)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            job_url,
            pr_number,
            repo,
            job_name,
            json.dumps(signature),
            datetime.utcnow().isoformat(),
            json.dumps(permafail_result)
        ))

        conn.commit()
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to store analysis for {job_url}: {e}")
    finally:
        if conn:
            conn.close()
