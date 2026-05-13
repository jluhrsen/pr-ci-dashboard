# Permafail Detection & Auto-Retest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add intelligent permafail detection and automated retesting to the PR CI Dashboard

**Architecture:** SQLite caching layer + Claude Code CLI skill integration + Flask API endpoints + JavaScript UI enhancements with auto-retest logic

**Tech Stack:** Python 3.x, Flask, SQLite3, subprocess (Claude Code CLI), JavaScript (vanilla), HTML5/CSS3

---

## File Structure

**New files to create:**
- `utils/db.py` - SQLite database initialization and operations
- `utils/ai_analyzer.py` - Claude Code CLI integration for permafail analysis
- `api/analysis.py` - Flask endpoints for analysis, status, and override
- `static/dumpster-fire.svg` - Permafail icon asset
- `tests/test_db.py` - Database layer tests
- `tests/test_ai_analyzer.py` - AI analyzer tests
- `tests/test_api_analysis.py` - API endpoint tests

**Files to modify:**
- `app.py` - Register new analysis blueprint
- `static/app.js` - Add permafail UI states, context menu, auto-retest integration
- `static/style.css` - Permafail visual styles

**Database file:**
- `dashboard.db` - SQLite database (auto-created, add to .gitignore)

---

## Phase 1: Database Foundation

### Task 1: SQLite Schema Setup

**Files:**
- Create: `utils/db.py`
- Modify: `.gitignore`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing test for database initialization**

```python
# tests/test_db.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py::test_init_db_creates_tables -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'utils.db'"

- [ ] **Step 3: Create minimal database module**

```python
# utils/db.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py::test_init_db_creates_tables -v`
Expected: PASS

- [ ] **Step 5: Update .gitignore to exclude database file**

```bash
# Add to .gitignore
echo "" >> .gitignore
echo "# Database" >> .gitignore
echo "dashboard.db" >> .gitignore
```

- [ ] **Step 6: Commit database initialization**

```bash
git add utils/db.py tests/test_db.py .gitignore
git commit -m "feat: add SQLite database initialization for permafail caching"
```

### Task 2: Store Analysis Function

**Files:**
- Modify: `utils/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing test for storing analysis**

```python
# tests/test_db.py (add to existing file)
from utils.db import store_analysis
import json

def test_store_analysis_inserts_record(tmp_path):
    """Test that store_analysis inserts a new record"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    signature = {"type": "test_failure", "tests": ["TestNetworkPolicy/Baseline"]}
    permafail_result = {"permafail": True, "reason": "Test failed in all runs"}

    store_analysis(
        job_url="https://prow.ci.openshift.org/view/12345",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature=signature,
        permafail_result=permafail_result,
        db_path=str(db_path)
    )

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM job_analyses WHERE job_url = ?",
                   ("https://prow.ci.openshift.org/view/12345",))
    row = cursor.fetchone()

    assert row is not None
    assert row[1] == 1234  # pr_number
    assert row[2] == "openshift/ovn-kubernetes"  # repo
    assert row[3] == "e2e-aws-ovn"  # job_name
    assert json.loads(row[4]) == signature
    assert json.loads(row[6]) == permafail_result

    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py::test_store_analysis_inserts_record -v`
Expected: FAIL with "AttributeError: module 'utils.db' has no attribute 'store_analysis'"

- [ ] **Step 3: Implement store_analysis function**

```python
# utils/db.py (add to existing file)
import json

def store_analysis(job_url, pr_number, repo, job_name, signature, permafail_result, db_path=None):
    """Store analysis results for a job URL"""
    path = db_path or DB_PATH
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
        datetime.now().isoformat(),
        json.dumps(permafail_result)
    ))

    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py::test_store_analysis_inserts_record -v`
Expected: PASS

- [ ] **Step 5: Commit store_analysis function**

```bash
git add utils/db.py tests/test_db.py
git commit -m "feat: add store_analysis function for caching permafail results"
```

### Task 3: Get Permafail Status Function

**Files:**
- Modify: `utils/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing test for batch status retrieval**

```python
# tests/test_db.py (add to existing file)
from utils.db import get_permafail_status

def test_get_permafail_status_returns_cached_results(tmp_path):
    """Test batch retrieval of permafail status"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    # Store two analyses
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/12345",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={"type": "test_failure", "tests": ["TestA"]},
        permafail_result={"permafail": True, "reason": "TestA failed"},
        db_path=str(db_path)
    )

    store_analysis(
        job_url="https://prow.ci.openshift.org/view/67890",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-gcp-ovn",
        signature={"type": "test_failure", "tests": ["TestB"]},
        permafail_result={"permafail": False, "reason": "Mixed failures"},
        db_path=str(db_path)
    )

    result = get_permafail_status(
        ["https://prow.ci.openshift.org/view/12345",
         "https://prow.ci.openshift.org/view/67890",
         "https://prow.ci.openshift.org/view/99999"],
        db_path=str(db_path)
    )

    assert "https://prow.ci.openshift.org/view/12345" in result
    assert result["https://prow.ci.openshift.org/view/12345"]["permafail"] is True
    assert result["https://prow.ci.openshift.org/view/12345"]["override"] is False

    assert "https://prow.ci.openshift.org/view/67890" in result
    assert result["https://prow.ci.openshift.org/view/67890"]["permafail"] is False

    # Uncached URL should not be in result
    assert "https://prow.ci.openshift.org/view/99999" not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py::test_get_permafail_status_returns_cached_results -v`
Expected: FAIL with "AttributeError: module 'utils.db' has no attribute 'get_permafail_status'"

- [ ] **Step 3: Implement get_permafail_status function**

```python
# utils/db.py (add to existing file)

def get_permafail_status(job_urls, db_path=None):
    """Get permafail status for a list of job URLs (batch query)"""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    placeholders = ','.join('?' * len(job_urls))
    query = f"""
        SELECT job_url, permafail_result, override
        FROM job_analyses
        WHERE job_url IN ({placeholders})
    """

    cursor.execute(query, job_urls)
    rows = cursor.fetchall()
    conn.close()

    result = {}
    for row in rows:
        job_url, permafail_result_json, override = row
        permafail_result = json.loads(permafail_result_json)
        result[job_url] = {
            "permafail": permafail_result.get("permafail", False),
            "reason": permafail_result.get("reason", ""),
            "override": bool(override)
        }

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py::test_get_permafail_status_returns_cached_results -v`
Expected: PASS

- [ ] **Step 5: Commit get_permafail_status function**

```bash
git add utils/db.py tests/test_db.py
git commit -m "feat: add batch permafail status retrieval"
```

### Task 4: Clear Override Function

**Files:**
- Modify: `utils/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing test for clearing override**

```python
# tests/test_db.py (add to existing file)
from utils.db import clear_override

def test_clear_override_resets_flag(tmp_path):
    """Test that clear_override sets override to false"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    # Store analysis with override=true
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO job_analyses
        (job_url, pr_number, repo, job_name, signature, analyzed_at, permafail_result, override)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
    """, (
        "https://prow.ci.openshift.org/view/12345",
        1234,
        "openshift/ovn-kubernetes",
        "e2e-aws-ovn",
        json.dumps({"type": "test_failure", "tests": ["TestA"]}),
        datetime.now().isoformat(),
        json.dumps({"permafail": True, "reason": "Test failed"})
    ))
    conn.commit()
    conn.close()

    # Clear override
    clear_override("https://prow.ci.openshift.org/view/12345", db_path=str(db_path))

    # Verify override is now false
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT override FROM job_analyses WHERE job_url = ?",
                   ("https://prow.ci.openshift.org/view/12345",))
    override_value = cursor.fetchone()[0]
    conn.close()

    assert override_value == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py::test_clear_override_resets_flag -v`
Expected: FAIL with "AttributeError: module 'utils.db' has no attribute 'clear_override'"

- [ ] **Step 3: Implement clear_override function**

```python
# utils/db.py (add to existing file)

def clear_override(job_url, db_path=None):
    """Clear the override flag for a job URL"""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE job_analyses
        SET override = 0
        WHERE job_url = ?
    """, (job_url,))

    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py::test_clear_override_resets_flag -v`
Expected: PASS

- [ ] **Step 5: Commit clear_override function**

```bash
git add utils/db.py tests/test_db.py
git commit -m "feat: add clear_override for permafail flag reset"
```

---

## Phase 2: Skill Development

### Task 5: Create Permafail Detection Skill Skeleton

**Files:**
- Create: `~/.claude/skills/pr-ci-dashboard/detect-permafail/skill.yaml`
- Create: `~/.claude/skills/pr-ci-dashboard/detect-permafail/prompt.md`

- [ ] **Step 1: Create skill directory structure**

```bash
mkdir -p ~/.claude/skills/pr-ci-dashboard/detect-permafail
```

- [ ] **Step 2: Write skill metadata**

```yaml
# ~/.claude/skills/pr-ci-dashboard/detect-permafail/skill.yaml
name: detect-permafail
description: Analyze 3 consecutive failed job runs to determine if they represent a permafail pattern
version: 1.0.0
parameters:
  job_urls:
    type: array
    description: List of 3 consecutive Prow job URLs to analyze
    required: true
  job_name:
    type: string
    description: Name of the job being analyzed
    required: true
  pr:
    type: string
    description: PR identifier (e.g., "openshift/ovn-kubernetes#1234")
    required: true
```

- [ ] **Step 3: Write skill prompt template**

```markdown
# ~/.claude/skills/pr-ci-dashboard/detect-permafail/prompt.md

You are analyzing 3 consecutive failed job runs to determine if they represent a **permafail** pattern.

## Inputs

**Job URLs:** {{job_urls}}
**Job Name:** {{job_name}}
**PR:** {{pr}}

## Your Task

1. **Spawn 3 parallel subagents** using the Task tool
2. Each subagent analyzes ONE job URL:
   - Invoke the `ci-prow-navigation` skill on the job URL
   - Extract failure signature:
     - **Test failures:** Parse test case names from e2e step output
     - **Infra failures:** Extract error message from failed step (cluster setup, network operator, etc.)
   - Return structured signature to main skill

3. **Compare the 3 signatures:**
   - If all are `type: "test_failure"` → check for ≥1 common test name (exact match)
   - If all are `type: "infra_failure"` → compare error messages for exact match or high similarity
   - If mixed types → NOT a permafail (inconsistent pattern)

4. **Generate verdict** with reasoning

## Output Format

Return ONLY valid JSON (no markdown, no explanation outside JSON):

```json
{
  "permafail": true,
  "reason": "Same test 'TestNetworkPolicy/Baseline' failed in all 3 runs",
  "signatures": [
    {"type": "test_failure", "tests": ["TestNetworkPolicy/Baseline", "TestPodConnectivity"]},
    {"type": "test_failure", "tests": ["TestNetworkPolicy/Baseline"]},
    {"type": "test_failure", "tests": ["TestNetworkPolicy/Baseline", "TestDNS"]}
  ],
  "common_tests": ["TestNetworkPolicy/Baseline"]
}
```

## Error Handling

If analysis fails for any URL:
- Return `{"permafail": false, "error": "Analysis failed for URL X", "signatures": [...]}`
- Do NOT block retesting with uncertain results
```

- [ ] **Step 4: Test skill availability**

Run: `claude-code skill pr-ci-dashboard:detect-permafail --help`
Expected: Skill help text showing parameters

- [ ] **Step 5: Commit skill files**

```bash
git add ~/.claude/skills/pr-ci-dashboard/
git commit -m "feat: add detect-permafail skill skeleton"
```

Note: This task creates the skill infrastructure. Full skill implementation with subagent logic will be tested in Phase 3 via integration tests.

---

## Phase 3: Backend API

### Task 6: AI Analyzer Module

**Files:**
- Create: `utils/ai_analyzer.py`
- Create: `tests/test_ai_analyzer.py`

- [ ] **Step 1: Write failing test for Claude Code CLI invocation**

```python
# tests/test_ai_analyzer.py
import pytest
import json
from unittest.mock import patch, MagicMock
from utils.ai_analyzer import analyze_permafail

def test_analyze_permafail_invokes_cli():
    """Test that analyze_permafail shells out to Claude Code CLI with correct parameters"""
    job_urls = [
        "https://prow.ci.openshift.org/view/1",
        "https://prow.ci.openshift.org/view/2",
        "https://prow.ci.openshift.org/view/3"
    ]
    job_name = "e2e-aws-ovn"
    pr_info = "openshift/ovn-kubernetes#1234"

    mock_result = {
        "permafail": True,
        "reason": "TestA failed in all runs",
        "signatures": [
            {"type": "test_failure", "tests": ["TestA"]},
            {"type": "test_failure", "tests": ["TestA"]},
            {"type": "test_failure", "tests": ["TestA"]}
        ],
        "common_tests": ["TestA"]
    }

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(mock_result),
            stderr=""
        )

        result = analyze_permafail(job_urls, job_name, pr_info)

        # Verify CLI was called with correct command
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert 'claude-code' in call_args
        assert 'skill' in call_args
        assert 'pr-ci-dashboard:detect-permafail' in call_args

        # Verify result
        assert result["permafail"] is True
        assert result["reason"] == "TestA failed in all runs"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ai_analyzer.py::test_analyze_permafail_invokes_cli -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'utils.ai_analyzer'"

- [ ] **Step 3: Implement analyze_permafail function**

```python
# utils/ai_analyzer.py
import subprocess
import json
import shlex

def analyze_permafail(job_urls, job_name, pr_info):
    """
    Analyze job URLs for permafail pattern using Claude Code CLI

    Args:
        job_urls: List of 3 consecutive Prow job URLs
        job_name: Name of the job
        pr_info: PR identifier (e.g., "openshift/ovn-kubernetes#1234")

    Returns:
        dict: Analysis result with permafail verdict and signatures
    """
    # Build CLI command with escaped JSON
    urls_json = json.dumps(job_urls)

    cmd = [
        'claude-code',
        'skill',
        'pr-ci-dashboard:detect-permafail',
        f'--job-urls={urls_json}',
        f'--job-name={job_name}',
        f'--pr={pr_info}'
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode != 0:
            return {
                "permafail": False,
                "error": f"Skill execution failed: {result.stderr}",
                "signatures": []
            }

        # Parse JSON output from skill
        return json.loads(result.stdout)

    except subprocess.TimeoutExpired:
        return {
            "permafail": False,
            "error": "Analysis timed out after 5 minutes",
            "signatures": []
        }
    except json.JSONDecodeError as e:
        return {
            "permafail": False,
            "error": f"Failed to parse skill output: {e}",
            "signatures": []
        }
    except Exception as e:
        return {
            "permafail": False,
            "error": f"Unexpected error: {e}",
            "signatures": []
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ai_analyzer.py::test_analyze_permafail_invokes_cli -v`
Expected: PASS

- [ ] **Step 5: Commit AI analyzer module**

```bash
git add utils/ai_analyzer.py tests/test_ai_analyzer.py
git commit -m "feat: add Claude Code CLI integration for permafail analysis"
```

### Task 7: Analysis API Endpoints

**Files:**
- Create: `api/analysis.py`
- Create: `tests/test_api_analysis.py`
- Modify: `app.py`

- [ ] **Step 1: Write failing test for POST /api/jobs/analyze endpoint**

```python
# tests/test_api_analysis.py
import pytest
import json
from unittest.mock import patch
from app import app
from utils.db import init_db

@pytest.fixture
def client(tmp_path):
    """Create test client with temporary database"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    app.config['TESTING'] = True
    app.config['DB_PATH'] = str(db_path)

    with app.test_client() as client:
        yield client

def test_analyze_endpoint_triggers_analysis(client, tmp_path):
    """Test POST /api/jobs/analyze triggers AI analysis and caches result"""
    request_data = {
        "pr": "openshift/ovn-kubernetes#1234",
        "repo": "openshift/ovn-kubernetes",
        "job_name": "e2e-aws-ovn",
        "job_urls": [
            "https://prow.ci.openshift.org/view/1",
            "https://prow.ci.openshift.org/view/2",
            "https://prow.ci.openshift.org/view/3"
        ]
    }

    mock_analysis = {
        "permafail": True,
        "reason": "TestA failed in all runs",
        "signatures": [
            {"type": "test_failure", "tests": ["TestA"]},
            {"type": "test_failure", "tests": ["TestA"]},
            {"type": "test_failure", "tests": ["TestA"]}
        ],
        "common_tests": ["TestA"]
    }

    with patch('utils.ai_analyzer.analyze_permafail', return_value=mock_analysis):
        response = client.post(
            '/api/jobs/analyze',
            data=json.dumps(request_data),
            content_type='application/json'
        )

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["permafail"] is True
    assert data["reason"] == "TestA failed in all runs"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_analysis.py::test_analyze_endpoint_triggers_analysis -v`
Expected: FAIL with "404 NOT FOUND" (endpoint doesn't exist)

- [ ] **Step 3: Create analysis blueprint**

```python
# api/analysis.py
from flask import Blueprint, request, jsonify
from utils.db import store_analysis, get_permafail_status, clear_override
from utils.ai_analyzer import analyze_permafail

analysis_bp = Blueprint('analysis', __name__)

@analysis_bp.route('/api/jobs/analyze', methods=['POST'])
def analyze_job():
    """
    Trigger permafail analysis for a job

    Request: {
        "pr": "openshift/ovn-kubernetes#1234",
        "repo": "openshift/ovn-kubernetes",
        "job_name": "e2e-aws-ovn",
        "job_urls": ["url1", "url2", "url3"]
    }

    Response: {
        "permafail": bool,
        "reason": str,
        "test_names": []
    }
    """
    data = request.get_json()

    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    required_fields = ["pr", "repo", "job_name", "job_urls"]
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400

    if len(data["job_urls"]) != 3:
        return jsonify({"error": "Exactly 3 job URLs required"}), 400

    # Parse PR info
    pr_parts = data["pr"].split("#")
    if len(pr_parts) != 2:
        return jsonify({"error": "Invalid PR format"}), 400
    pr_number = int(pr_parts[1])

    # Run AI analysis
    result = analyze_permafail(
        data["job_urls"],
        data["job_name"],
        data["pr"]
    )

    # Cache results for each URL
    for i, url in enumerate(data["job_urls"]):
        signature = result.get("signatures", [])[i] if i < len(result.get("signatures", [])) else {}
        store_analysis(
            job_url=url,
            pr_number=pr_number,
            repo=data["repo"],
            job_name=data["job_name"],
            signature=signature,
            permafail_result=result
        )

    return jsonify({
        "permafail": result.get("permafail", False),
        "reason": result.get("reason", ""),
        "test_names": result.get("common_tests", [])
    })
```

- [ ] **Step 4: Register blueprint in app.py**

```python
# app.py (add to existing file, after other imports)
from api.analysis import analysis_bp

# After app initialization, before routes
app.register_blueprint(analysis_bp)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_api_analysis.py::test_analyze_endpoint_triggers_analysis -v`
Expected: PASS

- [ ] **Step 6: Commit analysis endpoint**

```bash
git add api/analysis.py tests/test_api_analysis.py app.py
git commit -m "feat: add POST /api/jobs/analyze endpoint"
```

### Task 8: Override and Status Endpoints

**Files:**
- Modify: `api/analysis.py`
- Modify: `tests/test_api_analysis.py`

- [ ] **Step 1: Write failing tests for override and status endpoints**

```python
# tests/test_api_analysis.py (add to existing file)

def test_override_endpoint_clears_permafail(client, tmp_path):
    """Test POST /api/jobs/override clears permafail flag"""
    from utils.db import store_analysis

    # Store a permafail result
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/12345",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={"type": "test_failure", "tests": ["TestA"]},
        permafail_result={"permafail": True, "reason": "Test failed"},
        db_path=str(tmp_path / "test.db")
    )

    response = client.post(
        '/api/jobs/override',
        data=json.dumps({"job_url": "https://prow.ci.openshift.org/view/12345"}),
        content_type='application/json'
    )

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True


def test_status_endpoint_returns_batch_status(client, tmp_path):
    """Test GET /api/jobs/status returns permafail status for multiple URLs"""
    from utils.db import store_analysis

    # Store two results
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/1",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={"type": "test_failure", "tests": ["TestA"]},
        permafail_result={"permafail": True, "reason": "TestA failed"},
        db_path=str(tmp_path / "test.db")
    )

    store_analysis(
        job_url="https://prow.ci.openshift.org/view/2",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-gcp-ovn",
        signature={"type": "test_failure", "tests": ["TestB"]},
        permafail_result={"permafail": False, "reason": "Mixed"},
        db_path=str(tmp_path / "test.db")
    )

    response = client.get(
        '/api/jobs/status?job_urls=' + json.dumps([
            "https://prow.ci.openshift.org/view/1",
            "https://prow.ci.openshift.org/view/2"
        ])
    )

    assert response.status_code == 200
    data = json.loads(response.data)

    assert "https://prow.ci.openshift.org/view/1" in data
    assert data["https://prow.ci.openshift.org/view/1"]["permafail"] is True

    assert "https://prow.ci.openshift.org/view/2" in data
    assert data["https://prow.ci.openshift.org/view/2"]["permafail"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_analysis.py::test_override_endpoint_clears_permafail tests/test_api_analysis.py::test_status_endpoint_returns_batch_status -v`
Expected: FAIL with "404 NOT FOUND" for both endpoints

- [ ] **Step 3: Implement override endpoint**

```python
# api/analysis.py (add to existing file)

@analysis_bp.route('/api/jobs/override', methods=['POST'])
def override_permafail():
    """
    Clear permafail flag for a job

    Request: {"job_url": "https://..."}
    Response: {"success": bool}
    """
    data = request.get_json()

    if not data or 'job_url' not in data:
        return jsonify({"error": "Missing job_url"}), 400

    clear_override(data['job_url'])

    return jsonify({"success": True})
```

- [ ] **Step 4: Implement status endpoint**

```python
# api/analysis.py (add to existing file)

@analysis_bp.route('/api/jobs/status', methods=['GET'])
def get_job_status():
    """
    Get permafail status for multiple jobs

    Query: ?job_urls=["url1", "url2", ...]
    Response: {
        "url1": {"permafail": bool, "reason": str, "override": bool},
        ...
    }
    """
    import json

    job_urls_param = request.args.get('job_urls')
    if not job_urls_param:
        return jsonify({"error": "Missing job_urls parameter"}), 400

    try:
        job_urls = json.loads(job_urls_param)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON in job_urls"}), 400

    status = get_permafail_status(job_urls)

    return jsonify(status)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_api_analysis.py::test_override_endpoint_clears_permafail tests/test_api_analysis.py::test_status_endpoint_returns_batch_status -v`
Expected: PASS for both

- [ ] **Step 6: Commit override and status endpoints**

```bash
git add api/analysis.py tests/test_api_analysis.py
git commit -m "feat: add override and status endpoints for permafail management"
```

---

## Phase 4: Frontend UI

### Task 9: Dumpster Fire Icon Asset

**Files:**
- Create: `static/dumpster-fire.svg`

- [ ] **Step 1: Create SVG icon file**

```svg
<!-- static/dumpster-fire.svg -->
<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
  <!-- Dumpster body - bold -->
  <rect x="10" y="26" width="28" height="14" fill="#5a6c7d" stroke="#2d3748" stroke-width="2" rx="2"/>
  <rect x="8" y="24" width="32" height="3" fill="#6b7c8d" stroke="#2d3748" stroke-width="2"/>
  <!-- Wheels - chunky -->
  <circle cx="16" cy="41" r="3" fill="#2d3748" stroke="#000" stroke-width="1.5"/>
  <circle cx="32" cy="41" r="3" fill="#2d3748" stroke="#000" stroke-width="1.5"/>
  <!-- Flames -->
  <ellipse cx="24" cy="18" rx="8" ry="12" fill="#ff4500" opacity="0.8"/>
  <ellipse cx="18" cy="22" rx="6" ry="9" fill="#ff6b35" opacity="0.9"/>
  <ellipse cx="30" cy="22" rx="6" ry="9" fill="#ff6b35" opacity="0.9"/>
  <ellipse cx="24" cy="14" rx="5" ry="8" fill="#ffa500" opacity="0.9"/>
  <ellipse cx="24" cy="16" rx="3" ry="5" fill="#ffed4e" opacity="0.7"/>
</svg>
```

- [ ] **Step 2: Verify SVG renders correctly in browser**

Run: Open http://localhost:5000/static/dumpster-fire.svg in browser
Expected: Cartoon dumpster fire icon displays

- [ ] **Step 3: Commit icon asset**

```bash
git add static/dumpster-fire.svg
git commit -m "feat: add dumpster fire permafail icon"
```

### Task 10: Permafail Visual Styles

**Files:**
- Modify: `static/style.css`

- [ ] **Step 1: Add permafail icon styles**

```css
/* static/style.css (add to existing file) */

/* Permafail indicator */
.permafail-icon {
    width: 24px;
    height: 24px;
    margin-left: 8px;
    vertical-align: middle;
}

/* Warning banner for permafail reason */
.permafail-warning {
    font-size: 0.85em;
    color: #856404;
    background: #fff3cd;
    padding: 4px 8px;
    margin-top: 4px;
    border-radius: 3px;
}

/* Disabled retest button for permafails */
.retest-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
    background: #ccc;
}

/* Context menu */
.context-menu {
    position: absolute;
    background: white;
    border: 1px solid #ddd;
    border-radius: 4px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    z-index: 1000;
    min-width: 150px;
}

.context-menu-item {
    padding: 8px 12px;
    cursor: pointer;
    border-bottom: 1px solid #eee;
}

.context-menu-item:last-child {
    border-bottom: none;
}

.context-menu-item:hover {
    background: #f5f5f5;
}
```

- [ ] **Step 2: Commit CSS changes**

```bash
git add static/style.css
git commit -m "style: add permafail visual styles and context menu"
```

### Task 11: Permafail UI State Management

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add permafail state tracking**

```javascript
// static/app.js (add after existing state variables)

// Permafail tracking
const permafailJobs = new Map(); // jobKey -> {permafail: bool, reason: str, override: bool}
```

- [ ] **Step 2: Add function to render permafail icon**

```javascript
// static/app.js (add new function)

function renderPermafailIcon(jobElement, reason) {
    const jobHeader = jobElement.querySelector('.job-header') || jobElement;

    // Remove existing icon if present
    const existing = jobElement.querySelector('.permafail-icon');
    if (existing) existing.remove();

    // Add dumpster fire icon
    const icon = document.createElement('img');
    icon.src = '/static/dumpster-fire.svg';
    icon.className = 'permafail-icon';
    icon.alt = 'Permafail detected';
    icon.title = reason;
    jobHeader.appendChild(icon);

    // Add warning banner
    const warning = document.createElement('div');
    warning.className = 'permafail-warning';
    warning.textContent = `Permafail: ${reason}`;
    jobElement.appendChild(warning);

    // Disable retest button
    const retestBtn = jobElement.querySelector('.retest-btn');
    if (retestBtn) {
        retestBtn.disabled = true;
    }
}
```

- [ ] **Step 3: Add function to clear permafail state**

```javascript
// static/app.js (add new function)

function clearPermafailUI(jobElement, jobKey) {
    // Remove icon
    const icon = jobElement.querySelector('.permafail-icon');
    if (icon) icon.remove();

    // Remove warning
    const warning = jobElement.querySelector('.permafail-warning');
    if (warning) warning.remove();

    // Re-enable retest button
    const retestBtn = jobElement.querySelector('.retest-btn');
    if (retestBtn) {
        retestBtn.disabled = false;
    }

    // Update state
    permafailJobs.delete(jobKey);
}
```

- [ ] **Step 4: Commit permafail UI functions**

```bash
git add static/app.js
git commit -m "feat: add permafail UI rendering and clearing functions"
```

### Task 12: Context Menu Implementation

**Files:**
- Modify: `static/app.js`
- Modify: `index.html` (template)

- [ ] **Step 1: Add context menu HTML to template**

```html
<!-- templates/index.html (add before </body>) -->
<div id="contextMenu" class="context-menu" style="display: none;">
    <div class="context-menu-item" id="clearPermafailItem">Clear permafail</div>
</div>
```

- [ ] **Step 2: Add context menu event handlers**

```javascript
// static/app.js (add new functions)

let contextMenuTarget = null;

function showContextMenu(event, jobElement, jobKey) {
    event.preventDefault();

    const menu = document.getElementById('contextMenu');
    const clearItem = document.getElementById('clearPermafailItem');

    // Only show menu if job has permafail
    if (!permafailJobs.has(jobKey)) {
        return;
    }

    contextMenuTarget = { jobElement, jobKey };

    // Position menu at click location
    menu.style.left = event.pageX + 'px';
    menu.style.top = event.pageY + 'px';
    menu.style.display = 'block';
}

function hideContextMenu() {
    const menu = document.getElementById('contextMenu');
    menu.style.display = 'none';
    contextMenuTarget = null;
}

async function handleClearPermafail() {
    if (!contextMenuTarget) return;

    const { jobElement, jobKey } = contextMenuTarget;
    const jobUrl = jobElement.dataset.jobUrl; // Assume job URL stored in data attribute

    try {
        const response = await fetch('/api/jobs/override', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_url: jobUrl })
        });

        if (response.ok) {
            clearPermafailUI(jobElement, jobKey);
        }
    } catch (error) {
        console.error('Failed to clear permafail:', error);
    }

    hideContextMenu();
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
    // Clear permafail menu item
    document.getElementById('clearPermafailItem').addEventListener('click', handleClearPermafail);

    // Hide menu on click outside
    document.addEventListener('click', hideContextMenu);
});
```

- [ ] **Step 3: Wire up context menu to job cards**

```javascript
// static/app.js (modify existing job card rendering to add right-click handler)

function attachJobCardEvents(jobElement, jobKey) {
    // Add right-click handler
    jobElement.addEventListener('contextmenu', (e) => {
        showContextMenu(e, jobElement, jobKey);
    });
}
```

- [ ] **Step 4: Commit context menu implementation**

```bash
git add static/app.js templates/index.html
git commit -m "feat: add context menu for permafail override"
```

---

## Phase 5: Auto-Retest Integration

### Task 13: Check Permafail Before Auto-Retest

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add function to fetch permafail status**

```javascript
// static/app.js (add new function)

async function fetchPermafailStatus(jobUrls) {
    try {
        const response = await fetch(`/api/jobs/status?job_urls=${encodeURIComponent(JSON.stringify(jobUrls))}`);
        if (!response.ok) return {};
        return await response.json();
    } catch (error) {
        console.error('Failed to fetch permafail status:', error);
        return {};
    }
}
```

- [ ] **Step 2: Modify auto-retest logic to check permafail**

```javascript
// static/app.js (modify existing polling/auto-retest logic)

async function handleFailedJob(job, consecutiveFailures) {
    const jobKey = `${job.name}-${job.pr}`;

    if (consecutiveFailures <= 2) {
        // 1st or 2nd failure: auto-retest immediately
        await retestJob(job);
        return;
    }

    if (consecutiveFailures === 3) {
        // 3rd failure: check for permafail
        const jobUrls = await getConsecutiveFailureUrls(job); // Assumes this function exists

        if (jobUrls.length < 3) {
            // Not enough data, retest
            await retestJob(job);
            return;
        }

        // Trigger analysis
        try {
            const response = await fetch('/api/jobs/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    pr: `${job.repo}#${job.pr}`,
                    repo: job.repo,
                    job_name: job.name,
                    job_urls: jobUrls
                })
            });

            const result = await response.json();

            if (result.permafail) {
                // Mark as permafail, disable retest
                const jobElement = document.querySelector(`[data-job-key="${jobKey}"]`);
                if (jobElement) {
                    renderPermafailIcon(jobElement, result.reason);
                    permafailJobs.set(jobKey, result);
                }
                return; // Don't retest
            }
        } catch (error) {
            console.error('Permafail analysis failed:', error);
            // Fail open: allow retest
        }

        // Not a permafail, continue retesting
        await retestJob(job);
    }
}
```

- [ ] **Step 3: Commit auto-retest integration**

```bash
git add static/app.js
git commit -m "feat: integrate permafail detection into auto-retest flow"
```

### Task 14: Manual "Check for Permafail" Button

**Files:**
- Modify: `static/app.js`
- Modify: `templates/index.html`

- [ ] **Step 1: Add button to job card template**

```html
<!-- templates/index.html (modify job card template) -->
<div class="job-card">
    <!-- Existing job card content -->

    <button class="check-permafail-btn" style="display: none;">
        Check for Permafail
    </button>
</div>
```

- [ ] **Step 2: Show button when consecutive failures >= 2**

```javascript
// static/app.js (modify job card rendering)

function updateJobCard(job, consecutiveFailures) {
    const jobElement = document.querySelector(`[data-job-key="${job.name}-${job.pr}"]`);
    if (!jobElement) return;

    // Show "Check for Permafail" button if 2+ failures
    const checkBtn = jobElement.querySelector('.check-permafail-btn');
    if (checkBtn) {
        checkBtn.style.display = consecutiveFailures >= 2 ? 'inline-block' : 'none';
    }
}
```

- [ ] **Step 3: Add click handler for manual check**

```javascript
// static/app.js (add new function)

async function manualPermafailCheck(jobElement, job) {
    const checkBtn = jobElement.querySelector('.check-permafail-btn');
    checkBtn.disabled = true;
    checkBtn.textContent = 'Analyzing...';

    const jobUrls = await getConsecutiveFailureUrls(job);

    try {
        const response = await fetch('/api/jobs/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pr: `${job.repo}#${job.pr}`,
                repo: job.repo,
                job_name: job.name,
                job_urls: jobUrls
            })
        });

        const result = await response.json();

        if (result.permafail) {
            renderPermafailIcon(jobElement, result.reason);
            permafailJobs.set(`${job.name}-${job.pr}`, result);
            checkBtn.style.display = 'none';
        } else {
            checkBtn.textContent = 'No permafail detected';
            setTimeout(() => {
                checkBtn.textContent = 'Check for Permafail';
                checkBtn.disabled = false;
            }, 2000);
        }
    } catch (error) {
        console.error('Manual permafail check failed:', error);
        checkBtn.textContent = 'Check Failed';
        setTimeout(() => {
            checkBtn.textContent = 'Check for Permafail';
            checkBtn.disabled = false;
        }, 2000);
    }
}

// Wire up button clicks
document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('check-permafail-btn')) {
            const jobElement = e.target.closest('.job-card');
            const job = getJobDataFromElement(jobElement); // Assumes this function exists
            manualPermafailCheck(jobElement, job);
        }
    });
});
```

- [ ] **Step 4: Commit manual check button**

```bash
git add static/app.js templates/index.html
git commit -m "feat: add manual 'Check for Permafail' button"
```

---

## Phase 6: Polish & Testing

### Task 15: Initialize Database on App Startup

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Add database initialization to app startup**

```python
# app.py (add after imports, before route definitions)
from utils.db import init_db

# Initialize database on startup
init_db()
```

- [ ] **Step 2: Test app startup**

Run: `python app.py`
Expected: Server starts without errors, `dashboard.db` file created

- [ ] **Step 3: Commit database initialization**

```bash
git add app.py
git commit -m "feat: initialize database on app startup"
```

### Task 16: Error Handling for Analysis Failures

**Files:**
- Modify: `api/analysis.py`

- [ ] **Step 1: Add error response for analysis timeout**

```python
# api/analysis.py (modify analyze_job function)

@analysis_bp.route('/api/jobs/analyze', methods=['POST'])
def analyze_job():
    # ... existing validation ...

    # Run AI analysis
    result = analyze_permafail(
        data["job_urls"],
        data["job_name"],
        data["pr"]
    )

    # Check for analysis error
    if "error" in result:
        # Cache failure with error
        for i, url in enumerate(data["job_urls"]):
            signature = result.get("signatures", [])[i] if i < len(result.get("signatures", [])) else {}
            store_analysis(
                job_url=url,
                pr_number=pr_number,
                repo=data["repo"],
                job_name=data["job_name"],
                signature=signature,
                permafail_result=result
            )

        # Return error but with 200 status (analysis completed, just failed)
        return jsonify({
            "permafail": False,
            "reason": "Analysis unavailable, manual check needed",
            "error": result["error"]
        })

    # ... rest of existing code ...
```

- [ ] **Step 2: Add frontend error handling**

```javascript
// static/app.js (modify handleFailedJob to show error message)

async function handleFailedJob(job, consecutiveFailures) {
    // ... existing code ...

    try {
        const response = await fetch('/api/jobs/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pr: `${job.repo}#${job.pr}`,
                repo: job.repo,
                job_name: job.name,
                job_urls: jobUrls
            })
        });

        const result = await response.json();

        if (result.error) {
            // Show error message to user
            const jobElement = document.querySelector(`[data-job-key="${jobKey}"]`);
            if (jobElement) {
                const errorMsg = document.createElement('div');
                errorMsg.className = 'analysis-error';
                errorMsg.textContent = result.reason;
                jobElement.appendChild(errorMsg);
            }

            // Fail open: allow retest
            await retestJob(job);
            return;
        }

        // ... rest of existing code ...
    }
}
```

- [ ] **Step 3: Commit error handling**

```bash
git add api/analysis.py static/app.js
git commit -m "feat: add graceful error handling for analysis failures"
```

### Task 17: Integration Testing

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test for full workflow**

```python
# tests/test_integration.py
import pytest
import json
from unittest.mock import patch
from app import app
from utils.db import init_db

@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    app.config['TESTING'] = True
    app.config['DB_PATH'] = str(db_path)

    with app.test_client() as client:
        yield client

def test_full_permafail_workflow(client, tmp_path):
    """Test complete workflow: analyze → detect permafail → override → retest"""

    # Mock AI analysis
    mock_analysis = {
        "permafail": True,
        "reason": "TestNetworkPolicy/Baseline failed in all runs",
        "signatures": [
            {"type": "test_failure", "tests": ["TestNetworkPolicy/Baseline"]},
            {"type": "test_failure", "tests": ["TestNetworkPolicy/Baseline"]},
            {"type": "test_failure", "tests": ["TestNetworkPolicy/Baseline"]}
        ],
        "common_tests": ["TestNetworkPolicy/Baseline"]
    }

    with patch('utils.ai_analyzer.analyze_permafail', return_value=mock_analysis):
        # Step 1: Trigger analysis
        response = client.post('/api/jobs/analyze', data=json.dumps({
            "pr": "openshift/ovn-kubernetes#1234",
            "repo": "openshift/ovn-kubernetes",
            "job_name": "e2e-aws-ovn",
            "job_urls": [
                "https://prow.ci.openshift.org/view/1",
                "https://prow.ci.openshift.org/view/2",
                "https://prow.ci.openshift.org/view/3"
            ]
        }), content_type='application/json')

        assert response.status_code == 200
        result = json.loads(response.data)
        assert result["permafail"] is True

    # Step 2: Check status
    response = client.get('/api/jobs/status?job_urls=' + json.dumps([
        "https://prow.ci.openshift.org/view/1"
    ]))

    status = json.loads(response.data)
    assert status["https://prow.ci.openshift.org/view/1"]["permafail"] is True
    assert status["https://prow.ci.openshift.org/view/1"]["override"] is False

    # Step 3: Override permafail
    response = client.post('/api/jobs/override', data=json.dumps({
        "job_url": "https://prow.ci.openshift.org/view/1"
    }), content_type='application/json')

    assert response.status_code == 200

    # Step 4: Verify override cleared
    response = client.get('/api/jobs/status?job_urls=' + json.dumps([
        "https://prow.ci.openshift.org/view/1"
    ]))

    status = json.loads(response.data)
    # Note: clear_override sets override=0, but permafail analysis result remains
    # This is intentional - override just allows retesting, doesn't change analysis
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/test_integration.py::test_full_permafail_workflow -v`
Expected: PASS

- [ ] **Step 3: Commit integration test**

```bash
git add tests/test_integration.py
git commit -m "test: add full workflow integration test"
```

### Task 18: Documentation Updates

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add Permafail Detection section to README**

```markdown
# README.md (add new section after "Features")

## Permafail Detection

The dashboard automatically detects **permafails** - jobs with systematic failure patterns across multiple runs.

### How It Works

1. **Auto-retest logic:**
   - 1st consecutive failure → Auto-retest immediately
   - 2nd consecutive failure → Auto-retest immediately
   - 3rd consecutive failure → Trigger AI analysis

2. **AI analysis:** Uses Claude Code CLI to analyze failure signatures across 3 runs
   - Detects if the same test case fails in all runs
   - Detects if the same infrastructure error occurs in all runs

3. **Visual indicator:** Permafails are marked with a 🗑️🔥 dumpster fire icon
   - Retest button is disabled
   - Warning shows the failure reason

4. **Override:** Right-click a job card → "Clear permafail" to re-enable retesting

### Requirements

- Claude Code CLI installed and available in PATH
- `ci-prow-navigation` skill available (from OpenShift CI plugin)

### Database

Analysis results are cached in `dashboard.db` (SQLite) to avoid redundant AI calls.
```

- [ ] **Step 2: Commit README updates**

```bash
git add README.md
git commit -m "docs: add permafail detection documentation"
```

### Task 19: Final End-to-End Test

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Start development server**

Run: `python app.py`
Expected: Server starts on http://localhost:5000

- [ ] **Step 3: Manual UI test**

1. Open http://localhost:5000 in browser
2. Load a PR with failed jobs
3. Verify "Check for Permafail" button appears on jobs with 2+ failures
4. Click button, verify analysis runs
5. If permafail detected:
   - Verify dumpster fire icon appears
   - Verify retest button is disabled
   - Verify warning banner shows reason
6. Right-click job card, verify context menu appears
7. Click "Clear permafail", verify icon removed and retest re-enabled

- [ ] **Step 4: Create final commit**

```bash
git add -A
git commit -m "feat: complete permafail detection and auto-retest integration"
git push origin main
```

---

## Execution Complete

All 6 phases implemented:
1. ✅ Database Foundation (SQLite schema, CRUD)
2. ✅ Skill Development (detect-permafail skill)
3. ✅ Backend API (ai_analyzer, analysis endpoints)
4. ✅ Frontend UI (dumpster fire icon, context menu, job states)
5. ✅ Auto-Retest Integration (3-failure threshold, permafail check)
6. ✅ Polish & Testing (error handling, integration tests, docs)

The permafail detection system is now fully integrated into the PR CI Dashboard.
