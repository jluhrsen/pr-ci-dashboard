import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'dashboard.db')

def init_db(db_path=None):
    """Initialize database and create tables if they don't exist"""
    path = db_path or DB_PATH
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
            override BOOLEAN DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()
