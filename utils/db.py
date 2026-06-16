import sqlite3
import os
import json
from datetime import datetime, UTC

# Use persistent database location in user's home directory
# This ensures the database survives server restarts when using run.sh
DB_PATH = os.environ.get('PR_CI_DASHBOARD_DB',
                         os.path.expanduser('~/.local/share/pr-ci-dashboard/dashboard.db'))

def init_db(db_path=None):
    """Initialize database and create tables if they don't exist

    Args:
        db_path: Optional path to database file. Defaults to ~/.local/share/pr-ci-dashboard/dashboard.db

    Raises:
        RuntimeError: If database initialization fails.
    """
    path = db_path or DB_PATH

    # Ensure directory exists
    db_dir = os.path.dirname(path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

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
            datetime.now(UTC).isoformat(),
            json.dumps(permafail_result)
        ))

        conn.commit()
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to store analysis for {job_url}: {e}")
    finally:
        if conn:
            conn.close()


def get_permafail_status(job_urls, db_path=None):
    """
    Get permafail status for multiple job URLs

    Args:
        job_urls: List of Prow job URLs to check
        db_path: Optional database path (defaults to DB_PATH)

    Returns:
        dict: Map of job_url -> {permafail: bool, reason: str, override: bool}
              Only includes URLs that have cached analysis

    Raises:
        RuntimeError: If database operation fails
    """
    path = db_path or DB_PATH
    conn = None
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        # Handle empty list case
        if not job_urls:
            return {}

        # Use parameterized query with IN clause
        placeholders = ','.join('?' * len(job_urls))
        query = f"SELECT job_url, permafail_result, override FROM job_analyses WHERE job_url IN ({placeholders})"

        cursor.execute(query, job_urls)
        rows = cursor.fetchall()

        result = {}
        for row in rows:
            job_url = row[0]
            try:
                permafail_result = json.loads(row[1])
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON in database for job {job_url}: {e}")
            override = bool(row[2])

            result[job_url] = {
                "permafail": permafail_result.get("permafail", False),
                "reason": permafail_result.get("reason", ""),
                "override": override
            }

        return result
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to get permafail status: {e}")
    finally:
        if conn:
            conn.close()


def set_override(job_url, db_path=None):
    """
    Set override flag for a job URL to mark it as acknowledged/overridden

    Args:
        job_url: Prow job URL
        db_path: Optional database path (defaults to DB_PATH)

    Raises:
        RuntimeError: If database operation fails
    """
    path = db_path or DB_PATH
    conn = None
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        cursor.execute(
            "UPDATE job_analyses SET override = 1 WHERE job_url = ?",
            (job_url,)
        )

        conn.commit()
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to set override for {job_url}: {e}")
    finally:
        if conn:
            conn.close()


def clear_override(job_url, db_path=None):
    """
    Clear override flag for a job URL

    Args:
        job_url: Prow job URL
        db_path: Optional database path (defaults to DB_PATH)

    Raises:
        RuntimeError: If database operation fails
    """
    path = db_path or DB_PATH
    conn = None
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        cursor.execute(
            "UPDATE job_analyses SET override = 0 WHERE job_url = ?",
            (job_url,)
        )

        conn.commit()
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to clear override for {job_url}: {e}")
    finally:
        if conn:
            conn.close()


def delete_cached_analyses(job_urls, db_path=None):
    """
    Delete cached analysis for multiple job URLs

    Args:
        job_urls: List of Prow job URLs to delete
        db_path: Optional database path (defaults to DB_PATH)

    Returns:
        int: Number of records deleted

    Raises:
        RuntimeError: If database operation fails
    """
    path = db_path or DB_PATH
    conn = None
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        deleted_count = 0
        for url in job_urls:
            cursor.execute("DELETE FROM job_analyses WHERE job_url = ?", (url,))
            deleted_count += cursor.rowcount

        conn.commit()
        return deleted_count
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to delete cached analyses: {e}")
    finally:
        if conn:
            conn.close()
