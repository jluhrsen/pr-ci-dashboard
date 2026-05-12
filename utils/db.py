import sqlite3
import os

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
