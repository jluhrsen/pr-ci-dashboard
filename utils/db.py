import sqlite3
import os
import json
from datetime import datetime, UTC

# Use persistent database location in user's home directory
# This ensures the database survives server restarts when using run.sh
DB_PATH = os.environ.get('PR_CI_DASHBOARD_DB',
                         os.path.expanduser('~/.local/share/pr-ci-dashboard/dashboard.db'))


def is_permafail_result(permafail_result):
    """Return the permafail verdict from current or legacy analysis result shapes.

    Handles multiple schema formats:
    - {permafail: bool, ...}
    - {is_permafail: bool, ...}
    - {verdict: "PERMAFAIL"|"NOT_PERMAFAIL", ...}
    """
    if not isinstance(permafail_result, dict):
        return False

    # Check verdict field first (semantic truth)
    verdict = permafail_result.get("verdict")
    if isinstance(verdict, str):
        if verdict.upper() in ("PERMAFAIL", "PERM"):
            return True
        if verdict.upper() in ("NOT_PERMAFAIL", "NOT PERMAFAIL", "OK", "PASS"):
            return False

    # Fall back to boolean flags
    if "permafail" in permafail_result:
        return bool(permafail_result.get("permafail"))
    return bool(permafail_result.get("is_permafail", False))


def normalize_permafail_result(permafail_result):
    """Ensure analysis result dicts always expose the UI/API `permafail` key and reason.

    Synthesizes a reason if missing but verdict indicates permafail.
    """
    if not isinstance(permafail_result, dict):
        return {"permafail": False, "reason": ""}

    normalized = dict(permafail_result)
    normalized["permafail"] = is_permafail_result(permafail_result)

    # Synthesize reason if missing and result is permafail
    if normalized["permafail"] and not normalized.get("reason"):
        parts = []

        # Include verdict
        verdict = normalized.get("verdict")
        if verdict:
            parts.append(f"Verdict: {verdict}")

        # Include match statistics
        matching = normalized.get("matching_runs")
        comparable = normalized.get("comparable_runs")
        if matching is not None and comparable is not None:
            parts.append(f"{matching}/{comparable} runs matched")

        # Include confidence
        confidence = normalized.get("confidence")
        if confidence is not None:
            try:
                parts.append(f"Confidence: {float(confidence):.0%}")
            except (TypeError, ValueError):
                parts.append(f"Confidence: {confidence}")

        # Include failure type
        failure_type = normalized.get("failure_type")
        if failure_type:
            parts.append(f"Type: {failure_type}")

        # Include common tests or signature
        common_tests = normalized.get("all_common_tests")
        if common_tests and isinstance(common_tests, list) and len(common_tests) > 0:
            parts.append(f"Common test(s): {', '.join(common_tests[:3])}")
        elif "common_signature" in normalized:
            parts.append("Common signature detected")

        normalized["reason"] = "; ".join(parts) if parts else "Permafail detected (no details available)"

    return normalized


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
        try:
            os.makedirs(db_dir, exist_ok=True)
        except OSError as e:
            raise RuntimeError(f"Failed to initialize database at {path}: Cannot create directory {db_dir}: {e}")

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
    permafail_result = normalize_permafail_result(permafail_result)
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
            permafail_result = normalize_permafail_result(permafail_result)
            override = bool(row[2])

            result[job_url] = {
                "permafail": is_permafail_result(permafail_result),
                "reason": permafail_result.get("reason", ""),
                "override": override
            }

        return result
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to get permafail status: {e}")
    finally:
        if conn:
            conn.close()


def get_pr_permafail_status(repo, pr_number, db_path=None):
    """
    Get permafail status for all jobs in a PR

    Args:
        repo: Repository (e.g., "openshift/ovn-kubernetes")
        pr_number: PR number
        db_path: Optional database path (defaults to DB_PATH)

    Returns:
        dict: Map of job_name -> {permafail: bool, reason: str, override: bool, job_urls: list}
              Groups all URLs for the same job_name together
              Only includes jobs that have at least one cached permafail=True result

    Raises:
        RuntimeError: If database operation fails
    """
    path = db_path or DB_PATH
    conn = None
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        # Query all analyses for this PR
        query = """
            SELECT job_name, job_url, permafail_result, override
            FROM job_analyses
            WHERE repo = ? AND pr_number = ?
        """

        cursor.execute(query, (repo, pr_number))
        rows = cursor.fetchall()

        # Group by job_name
        jobs = {}
        for row in rows:
            job_name = row[0]
            job_url = row[1]
            try:
                permafail_result = json.loads(row[2])
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON in database for job {job_url}: {e}")
            permafail_result = normalize_permafail_result(permafail_result)
            override = bool(row[3])

            # Only include if permafail=True (ignore non-permafail cached results)
            if is_permafail_result(permafail_result) and not override:
                if job_name not in jobs:
                    jobs[job_name] = {
                        "permafail": True,
                        "reason": permafail_result.get("reason", ""),
                        "override": False,
                        "job_urls": []
                    }
                jobs[job_name]["job_urls"].append(job_url)

        return jobs
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to get PR permafail status: {e}")
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
