# PR CI Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local web dashboard that shows OpenShift PR job failures with one-click retest functionality.

**Architecture:** Flask backend fetches bash scripts from GitHub, executes them via subprocess to get job status, parses text output. Vanilla JS frontend progressively loads PR cards with expandable e2e/payload sections. Uses local `gh` CLI for posting retest comments.

**Tech Stack:** Flask, Python subprocess/requests, vanilla HTML/CSS/JS, GitHub API via `gh` CLI

---

## Task 1: Script Fetcher Module

**Files:**
- Create: `utils/__init__.py`
- Create: `utils/script_fetcher.py`

**Step 1: Create utils package**

```bash
touch utils/__init__.py
```

**Step 2: Write script fetcher**

Create `utils/script_fetcher.py`:

```python
"""Fetch bash scripts from GitHub on startup."""
import os
import requests

# Script URLs from PR #177 (update to 'main' after merge)
E2E_SCRIPT_URL = "https://raw.githubusercontent.com/openshift-eng/ai-helpers/add-ci-pr-retest-command/plugins/ci/skills/e2e-retest/e2e-retest.sh"
PAYLOAD_SCRIPT_URL = "https://raw.githubusercontent.com/openshift-eng/ai-helpers/add-ci-pr-retest-command/plugins/ci/skills/payload-retest/payload-retest.sh"

SCRIPT_DIR = "/tmp/pr-ci-dashboard"


def fetch_scripts():
    """Download scripts from GitHub to local temp directory."""
    os.makedirs(SCRIPT_DIR, exist_ok=True)

    scripts = {
        'e2e-retest.sh': E2E_SCRIPT_URL,
        'payload-retest.sh': PAYLOAD_SCRIPT_URL
    }

    for filename, url in scripts.items():
        local_path = os.path.join(SCRIPT_DIR, filename)

        print(f"Fetching {filename} from GitHub...")
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        with open(local_path, 'w') as f:
            f.write(response.text)

        os.chmod(local_path, 0o755)
        print(f"✅ {filename} ready at {local_path}")

    return SCRIPT_DIR


def get_script_path(script_name):
    """Get full path to a fetched script."""
    return os.path.join(SCRIPT_DIR, script_name)
```

**Step 3: Test script fetcher manually**

Run in Python REPL:
```python
from utils.script_fetcher import fetch_scripts
fetch_scripts()
# Should print success messages and create files in /tmp/pr-ci-dashboard
```

**Step 4: Commit**

```bash
git add utils/
git commit -m "feat: add script fetcher to download bash scripts from GitHub

Fetches e2e-retest.sh and payload-retest.sh from PR #177 on startup.
Downloads to /tmp/pr-ci-dashboard and makes executable.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 2: E2E Output Parser

**Files:**
- Create: `parsers/__init__.py`
- Create: `parsers/e2e_parser.py`

**Step 1: Create parsers package**

```bash
touch parsers/__init__.py
```

**Step 2: Write e2e parser**

Create `parsers/e2e_parser.py`:

```python
"""Parse e2e-retest.sh output."""
import re


def parse_e2e_output(output: str) -> dict:
    """
    Parse e2e-retest.sh text output.

    Expected format:
        Failed e2e jobs:
          ❌ e2e-aws-ovn
             Consecutive failures: 5
             Recent history: 8 fail / 2 pass / 0 abort
        ⏳ Currently running (2 jobs):
          • e2e-metal-ipi

    Returns:
        {
            "failed": [{"name": "job-name", "consecutive": 5}, ...],
            "running": ["job-name", ...]
        }
    """
    failed_jobs = []
    running_jobs = []

    # Extract failed jobs with consecutive count
    # Pattern: ❌ <job-name>\n     Consecutive failures: <num>
    failed_pattern = r'❌ (.+?)\n\s+Consecutive failures: (\d+)'
    for match in re.finditer(failed_pattern, output, re.MULTILINE):
        job_name = match.group(1).strip()
        consecutive = int(match.group(2))
        failed_jobs.append({"name": job_name, "consecutive": consecutive})

    # Extract running jobs
    # Pattern: • <job-name>
    running_section = re.search(
        r'Currently running.*?:\n(.*?)(?:\n\n|$)',
        output,
        re.DOTALL
    )
    if running_section:
        running_pattern = r'• (.+?)(?:\n|$)'
        for match in re.finditer(running_pattern, running_section.group(1)):
            running_jobs.append(match.group(1).strip())

    return {"failed": failed_jobs, "running": running_jobs}
```

**Step 3: Test parser manually**

Create test file with sample output:
```bash
cat > /tmp/test_e2e_output.txt << 'EOF'
Failed e2e jobs:
  ❌ e2e-aws-ovn
     Consecutive failures: 5
     Recent history: 8 fail / 2 pass / 0 abort
  ❌ e2e-gcp-ovn
     Consecutive failures: 2
     Recent history: 2 fail / 3 pass / 0 abort
⏳ Currently running (1 jobs):
  • e2e-metal-ipi
EOF
```

Test in Python REPL:
```python
from parsers.e2e_parser import parse_e2e_output
output = open('/tmp/test_e2e_output.txt').read()
result = parse_e2e_output(output)
print(result)
# Should show: {"failed": [{"name": "e2e-aws-ovn", "consecutive": 5}, ...], "running": ["e2e-metal-ipi"]}
```

**Step 4: Commit**

```bash
git add parsers/
git commit -m "feat: add e2e script output parser

Parses text output from e2e-retest.sh to extract failed jobs
with consecutive counts and currently running jobs.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Payload Output Parser

**Files:**
- Create: `parsers/payload_parser.py`

**Step 1: Write payload parser**

Create `parsers/payload_parser.py`:

```python
"""Parse payload-retest.sh output."""
import re


def parse_payload_output(output: str) -> dict:
    """
    Parse payload-retest.sh text output.

    Expected format:
        Failed payload jobs:
          ❌ periodic-ci-openshift-ovn-kubernetes-release-4.18-e2e-aws-ovn
             Consecutive failures: 3
        ⏳ Currently running (1 jobs):
          • periodic-ci-...

    Returns:
        {
            "failed": [{"name": "job-name", "consecutive": 3}, ...],
            "running": ["job-name", ...]
        }
    """
    failed_jobs = []
    running_jobs = []

    # Extract failed jobs with consecutive count
    failed_pattern = r'❌ (.+?)\n\s+Consecutive failures: (\d+)'
    for match in re.finditer(failed_pattern, output, re.MULTILINE):
        job_name = match.group(1).strip()
        consecutive = int(match.group(2))
        failed_jobs.append({"name": job_name, "consecutive": consecutive})

    # Extract running jobs
    running_section = re.search(
        r'Currently running.*?:\n(.*?)(?:\n\n|$)',
        output,
        re.DOTALL
    )
    if running_section:
        running_pattern = r'• (.+?)(?:\n|$)'
        for match in re.finditer(running_pattern, running_section.group(1)):
            running_jobs.append(match.group(1).strip())

    return {"failed": failed_jobs, "running": running_jobs}
```

**Step 2: Test parser manually**

Create test file:
```bash
cat > /tmp/test_payload_output.txt << 'EOF'
Failed payload jobs:
  ❌ periodic-ci-openshift-ovn-kubernetes-release-4.18-e2e-aws-ovn
     Consecutive failures: 3
⏳ Currently running (0 jobs):
EOF
```

Test in Python REPL:
```python
from parsers.payload_parser import parse_payload_output
output = open('/tmp/test_payload_output.txt').read()
result = parse_payload_output(output)
print(result)
# Should show: {"failed": [{"name": "periodic-ci-...", "consecutive": 3}], "running": []}
```

**Step 3: Commit**

```bash
git add parsers/payload_parser.py
git commit -m "feat: add payload script output parser

Parses text output from payload-retest.sh to extract failed jobs
with consecutive counts and running jobs.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Job Executor Module

**Files:**
- Create: `utils/job_executor.py`

**Step 1: Write job executor**

Create `utils/job_executor.py`:

```python
"""Execute bash scripts and parse output."""
import subprocess
from parsers.e2e_parser import parse_e2e_output
from parsers.payload_parser import parse_payload_output
from utils.script_fetcher import get_script_path


def get_e2e_jobs(repo: str, pr_number: int) -> dict:
    """
    Execute e2e-retest.sh and parse output.

    Returns:
        {"failed": [...], "running": [...]} or {"error": "message"}
    """
    script_path = get_script_path('e2e-retest.sh')

    try:
        # Pipe "4" to select "Just show list (done)"
        result = subprocess.run(
            ["bash", script_path, repo, str(pr_number)],
            input="4\n",
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return {
                "error": "Script failed",
                "stderr": result.stderr,
                "failed": [],
                "running": []
            }

        return parse_e2e_output(result.stdout)

    except subprocess.TimeoutExpired:
        return {
            "error": "Script timed out",
            "failed": [],
            "running": []
        }
    except Exception as e:
        return {
            "error": str(e),
            "failed": [],
            "running": []
        }


def get_payload_jobs(repo: str, pr_number: int) -> dict:
    """
    Execute payload-retest.sh and parse output.

    Returns:
        {"failed": [...], "running": [...]} or {"error": "message"}
    """
    script_path = get_script_path('payload-retest.sh')

    try:
        # Pipe "3" to select "Just show list (done)"
        result = subprocess.run(
            ["bash", script_path, repo, str(pr_number)],
            input="3\n",
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return {
                "error": "Script failed",
                "stderr": result.stderr,
                "failed": [],
                "running": []
            }

        return parse_payload_output(result.stdout)

    except subprocess.TimeoutExpired:
        return {
            "error": "Script timed out",
            "failed": [],
            "running": []
        }
    except Exception as e:
        return {
            "error": str(e),
            "failed": [],
            "running": []
        }
```

**Step 2: Commit**

```bash
git add utils/job_executor.py
git commit -m "feat: add job executor to run bash scripts

Executes e2e-retest.sh and payload-retest.sh via subprocess,
handles timeouts and errors, returns parsed job data.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 5: GitHub CLI Auth Checker

**Files:**
- Create: `utils/gh_auth.py`

**Step 1: Write auth checker**

Create `utils/gh_auth.py`:

```python
"""Check GitHub CLI authentication status."""
import subprocess


def check_gh_auth() -> dict:
    """
    Check if gh CLI is installed and authenticated.

    Returns:
        {
            "authenticated": bool,
            "error": str or None
        }
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=5
        )

        # gh auth status returns 0 if authenticated
        if result.returncode == 0:
            return {"authenticated": True, "error": None}
        else:
            return {
                "authenticated": False,
                "error": "Not authenticated. Run: gh auth login"
            }

    except FileNotFoundError:
        return {
            "authenticated": False,
            "error": "GitHub CLI not found. Install from: https://cli.github.com"
        }
    except Exception as e:
        return {
            "authenticated": False,
            "error": f"Error checking auth: {str(e)}"
        }


def post_retest_comment(owner: str, repo: str, pr: int, comment_body: str) -> dict:
    """
    Post a comment to a PR using gh CLI.

    Returns:
        {"success": True} or {"error": "message"}
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "comment", str(pr),
             "--repo", f"{owner}/{repo}",
             "--body", comment_body],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            # Check if auth error
            if "authentication" in result.stderr.lower():
                return {"error": "auth_failed"}
            return {"error": result.stderr}

        return {"success": True}

    except Exception as e:
        return {"error": str(e)}
```

**Step 2: Test auth checker manually**

Test in Python REPL:
```python
from utils.gh_auth import check_gh_auth
result = check_gh_auth()
print(result)
# Should show: {"authenticated": True, "error": None} if gh is set up
```

**Step 3: Commit**

```bash
git add utils/gh_auth.py
git commit -m "feat: add GitHub CLI auth checker and comment poster

Checks gh auth status on startup and posts retest comments to PRs.
Handles auth errors gracefully.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Flask Server Skeleton

**Files:**
- Create: `server.py`

**Step 1: Write Flask server skeleton**

Create `server.py`:

```python
"""Flask server for PR CI Dashboard."""
import sys
from flask import Flask, jsonify, request, render_template
from utils.script_fetcher import fetch_scripts
from utils.gh_auth import check_gh_auth

app = Flask(__name__)

# Global state
DEFAULT_QUERY = "is:pr is:open archived:false author:openshift-pr-manager[bot]"
CLI_ARGS = []


@app.route('/')
def index():
    """Serve main dashboard page."""
    return render_template('index.html')


@app.route('/api/auth/status')
def auth_status():
    """Check GitHub CLI authentication status."""
    return jsonify(check_gh_auth())


@app.route('/api/default-query')
def default_query():
    """Get the default search query (base + CLI args)."""
    query = DEFAULT_QUERY
    if CLI_ARGS:
        query += " " + " ".join(CLI_ARGS)
    return jsonify({"query": query})


def parse_cli_args():
    """Parse CLI arguments as search query additions."""
    global CLI_ARGS
    # Skip script name, collect remaining args
    CLI_ARGS = sys.argv[1:]
    print(f"CLI args: {CLI_ARGS}")


def main():
    """Start the Flask server."""
    print("🚀 PR CI Dashboard Starting...")

    # Parse CLI arguments
    parse_cli_args()

    # Fetch scripts from GitHub
    try:
        fetch_scripts()
    except Exception as e:
        print(f"❌ Failed to fetch scripts: {e}")
        print("Cannot start dashboard without scripts.")
        sys.exit(1)

    # Check gh auth
    auth = check_gh_auth()
    if not auth["authenticated"]:
        print(f"⚠️  {auth['error']}")
        print("Dashboard will start but retest buttons will be disabled.")
    else:
        print("✅ GitHub CLI authenticated")

    print("\n🌐 Dashboard running at http://localhost:5000")
    print(f"📝 Default search: {DEFAULT_QUERY}")
    if CLI_ARGS:
        print(f"   + CLI args: {' '.join(CLI_ARGS)}")

    app.run(host='0.0.0.0', port=5000, debug=True)


if __name__ == '__main__':
    main()
```

**Step 2: Create minimal HTML template**

Create `templates/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PR CI Dashboard</title>
</head>
<body>
    <h1>PR CI Dashboard</h1>
    <p>Loading...</p>
</body>
</html>
```

**Step 3: Test server starts**

Run:
```bash
python server.py --author:jluhrsen
```

Expected output:
```
🚀 PR CI Dashboard Starting...
CLI args: ['--author:jluhrsen']
Fetching e2e-retest.sh from GitHub...
✅ e2e-retest.sh ready at /tmp/pr-ci-dashboard/e2e-retest.sh
...
🌐 Dashboard running at http://localhost:5000
```

Visit http://localhost:5000 - should see "PR CI Dashboard" page.

**Step 4: Commit**

```bash
git add server.py templates/
git commit -m "feat: add Flask server skeleton with script fetching

Server fetches scripts on startup, checks gh auth, parses CLI args,
serves basic HTML template. Ready for API endpoints.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 7: PR Search API Endpoint

**Files:**
- Create: `api/__init__.py`
- Create: `api/search.py`
- Modify: `server.py` (add import and route)

**Step 1: Create API package**

```bash
touch api/__init__.py
```

**Step 2: Write search API**

Create `api/search.py`:

```python
"""PR search via GitHub CLI."""
import subprocess
import json


def search_prs(query: str, page: int = 1, per_page: int = 10) -> dict:
    """
    Search PRs using GitHub CLI.

    Returns:
        {
            "prs": [
                {
                    "number": 123,
                    "title": "...",
                    "owner": "openshift",
                    "repo": "ovn-kubernetes",
                    "author": "user",
                    "created_at": "2024-01-01T00:00:00Z",
                    "state": "OPEN"
                },
                ...
            ],
            "total": 47
        }
    """
    try:
        # Use gh search to find PRs
        # Format: owner/repo#number
        result = subprocess.run(
            ["gh", "search", "prs", query, "--limit", str(per_page), "--json", "number,title,repository,author,createdAt,state"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            return {"error": result.stderr, "prs": [], "total": 0}

        raw_prs = json.loads(result.stdout)

        # Transform to our format
        prs = []
        for pr in raw_prs:
            repo_full = pr.get("repository", {})
            owner = repo_full.get("owner", {}).get("login", "")
            repo = repo_full.get("name", "")

            prs.append({
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "owner": owner,
                "repo": repo,
                "author": pr.get("author", {}).get("login", ""),
                "created_at": pr.get("createdAt", ""),
                "state": pr.get("state", "UNKNOWN")
            })

        return {"prs": prs, "total": len(prs)}

    except Exception as e:
        return {"error": str(e), "prs": [], "total": 0}
```

**Step 3: Add route to server**

Modify `server.py` - add after imports:

```python
from api.search import search_prs
```

Add route before `main()`:

```python
@app.route('/api/search', methods=['POST'])
def api_search():
    """Search for PRs."""
    data = request.get_json()
    query = data.get('query', '')
    page = data.get('page', 1)
    per_page = data.get('per_page', 10)

    result = search_prs(query, page, per_page)
    return jsonify(result)
```

**Step 4: Test search API**

Start server, then in another terminal:
```bash
curl -X POST http://localhost:5000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "repo:openshift/ovn-kubernetes is:pr is:open"}'
```

Expected: JSON with PR list

**Step 5: Commit**

```bash
git add api/ server.py
git commit -m "feat: add PR search API endpoint

Uses gh search to find PRs matching query. Returns standardized
PR data with owner/repo/number for job fetching.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 8: PR Jobs API Endpoint

**Files:**
- Create: `api/jobs.py`
- Modify: `server.py` (add import and route)

**Step 1: Write jobs API**

Create `api/jobs.py`:

```python
"""Fetch job status for a PR."""
from concurrent.futures import ThreadPoolExecutor
from utils.job_executor import get_e2e_jobs, get_payload_jobs


def get_pr_jobs(owner: str, repo: str, pr_number: int) -> dict:
    """
    Fetch e2e and payload job status for a PR.

    Runs both scripts in parallel for speed.

    Returns:
        {
            "pr": {"owner": "...", "repo": "...", "number": 123},
            "e2e": {"failed": [...], "running": [...]},
            "payload": {"failed": [...], "running": [...]}
        }
    """
    repo_full = f"{owner}/{repo}"

    # Run both in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        e2e_future = executor.submit(get_e2e_jobs, repo_full, pr_number)
        payload_future = executor.submit(get_payload_jobs, repo_full, pr_number)

        e2e_result = e2e_future.result()
        payload_result = payload_future.result()

    return {
        "pr": {
            "owner": owner,
            "repo": repo,
            "number": pr_number
        },
        "e2e": e2e_result,
        "payload": payload_result
    }
```

**Step 2: Add route to server**

Modify `server.py` - add after imports:

```python
from api.jobs import get_pr_jobs
```

Add route before `main()`:

```python
@app.route('/api/pr/<owner>/<repo>/<int:pr_number>')
def api_pr_jobs(owner, repo, pr_number):
    """Get job status for a PR."""
    result = get_pr_jobs(owner, repo, pr_number)
    return jsonify(result)
```

**Step 3: Test jobs API**

Start server, then test:
```bash
curl http://localhost:5000/api/pr/openshift/ovn-kubernetes/2838
```

Expected: JSON with e2e and payload job data (may take 30+ seconds)

**Step 4: Commit**

```bash
git add api/jobs.py server.py
git commit -m "feat: add PR jobs API endpoint

Fetches e2e and payload job status by executing bash scripts
in parallel. Returns failed/running jobs with consecutive counts.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 9: Retest API Endpoint

**Files:**
- Create: `api/retest.py`
- Modify: `server.py` (add import and route)

**Step 1: Write retest API**

Create `api/retest.py`:

```python
"""Post retest comments to PRs."""
from utils.gh_auth import post_retest_comment


def retest_jobs(owner: str, repo: str, pr: int, jobs: list, job_type: str) -> dict:
    """
    Post retest comment for jobs.

    Args:
        owner: GitHub org/user
        repo: Repository name
        pr: PR number
        jobs: List of job names
        job_type: "e2e" or "payload"

    Returns:
        {"success": True} or {"error": "message"}
    """
    if not jobs:
        return {"error": "No jobs specified"}

    # Build comment body
    if job_type == "e2e":
        lines = [f"/test {job}" for job in jobs]
    elif job_type == "payload":
        lines = [f"/payload-job {job}" for job in jobs]
    else:
        return {"error": f"Invalid job type: {job_type}"}

    comment_body = "\n".join(lines)

    return post_retest_comment(owner, repo, pr, comment_body)
```

**Step 2: Add route to server**

Modify `server.py` - add after imports:

```python
from api.retest import retest_jobs
```

Add route before `main()`:

```python
@app.route('/api/retest', methods=['POST'])
def api_retest():
    """Post retest comment to PR."""
    data = request.get_json()

    owner = data.get('owner')
    repo = data.get('repo')
    pr = data.get('pr')
    jobs = data.get('jobs', [])
    job_type = data.get('type', 'e2e')

    if not all([owner, repo, pr, jobs]):
        return jsonify({"error": "Missing required fields"}), 400

    result = retest_jobs(owner, repo, pr, jobs, job_type)
    return jsonify(result)
```

**Step 3: Test retest API**

Start server, test with a real PR (only if you want to actually post a comment!):
```bash
curl -X POST http://localhost:5000/api/retest \
  -H 'Content-Type: application/json' \
  -d '{
    "owner": "openshift",
    "repo": "ovn-kubernetes",
    "pr": 2838,
    "jobs": ["e2e-aws-ovn"],
    "type": "e2e"
  }'
```

Expected: `{"success": true}` or auth error

**Step 4: Commit**

```bash
git add api/retest.py server.py
git commit -m "feat: add retest API endpoint

Posts /test or /payload-job comments to PRs via gh CLI.
Handles auth errors gracefully.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 10: Frontend HTML Structure

**Files:**
- Modify: `templates/index.html`

**Step 1: Write full HTML with Red Hat theme**

Replace `templates/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PR CI Dashboard</title>
    <style>
        :root {
            --primary: #ee0000;
            --primary-dark: #c00000;
            --bg-dark: #0f0f0f;
            --bg-card: #1a1a1a;
            --text-primary: #f5f5f5;
            --text-secondary: #d4d4d4;
            --border: #3d3d3d;
            --success: #92cc6f;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-dark);
            color: var(--text-primary);
            line-height: 1.6;
        }

        .navbar {
            background: var(--bg-card);
            border-bottom: 1px solid var(--border);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
        }

        h1 {
            font-size: 1.5rem;
            color: var(--primary);
        }

        .btn {
            background: var(--primary);
            color: white;
            border: none;
            padding: 0.5rem 1rem;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.9rem;
        }

        .btn:hover {
            background: var(--primary-dark);
        }

        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }

        .btn-secondary {
            background: var(--bg-card);
            border: 1px solid var(--border);
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }

        .banner {
            background: #856404;
            color: #fff3cd;
            padding: 1rem;
            margin-bottom: 1rem;
            border-radius: 4px;
        }

        .banner.hidden {
            display: none;
        }

        .search-box {
            margin-bottom: 2rem;
        }

        .search-input {
            width: 100%;
            padding: 0.75rem;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 4px;
            color: var(--text-primary);
            font-size: 1rem;
            margin-bottom: 0.5rem;
        }

        .pr-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.5rem;
            margin-bottom: 1rem;
        }

        .pr-header {
            margin-bottom: 1rem;
        }

        .pr-title {
            font-size: 1.1rem;
            margin-bottom: 0.5rem;
        }

        .pr-meta {
            font-size: 0.9rem;
            color: var(--text-secondary);
        }

        .job-section {
            margin-top: 1rem;
            border-top: 1px solid var(--border);
            padding-top: 1rem;
        }

        .job-section-header {
            cursor: pointer;
            padding: 0.5rem 0;
            user-select: none;
        }

        .job-section-header:hover {
            color: var(--primary);
        }

        .job-list {
            margin-top: 0.5rem;
            display: none;
        }

        .job-list.expanded {
            display: block;
        }

        .job-item {
            padding: 0.75rem;
            margin: 0.5rem 0;
            background: var(--bg-dark);
            border-radius: 4px;
        }

        .job-name {
            font-family: monospace;
            margin-bottom: 0.5rem;
        }

        .job-actions {
            display: flex;
            gap: 0.5rem;
            margin-top: 0.5rem;
        }

        .loading {
            text-align: center;
            padding: 2rem;
            color: var(--text-secondary);
        }

        .toast-container {
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            z-index: 1000;
        }

        .toast {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 4px;
            padding: 1rem;
            margin-top: 0.5rem;
            min-width: 300px;
            animation: slideIn 0.3s ease;
        }

        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }

        .toast.success { border-left: 4px solid var(--success); }
        .toast.error { border-left: 4px solid var(--primary); }

        #load-more-btn {
            width: 100%;
            margin-top: 2rem;
        }
    </style>
</head>
<body>
    <div class="navbar">
        <h1>PR CI Dashboard</h1>
        <button class="btn" id="refresh-btn">Refresh</button>
    </div>

    <div id="auth-banner" class="banner hidden"></div>

    <div class="container">
        <div class="search-box">
            <input type="text" id="search-input" class="search-input" placeholder="Loading default query...">
            <button class="btn" id="search-btn">Search</button>
        </div>

        <div id="pr-cards-container"></div>

        <button class="btn btn-secondary hidden" id="load-more-btn">
            Load More (<span id="current-count">0</span> of <span id="total-count">0</span>)
        </button>
    </div>

    <div class="toast-container" id="toast-container"></div>

    <script src="/static/app.js"></script>
</body>
</html>
```

**Step 2: Commit**

```bash
git add templates/index.html
git commit -m "feat: add complete HTML structure with Red Hat theme

Includes navbar, search box, PR card containers, toast notifications.
Dark theme with --primary: #ee0000 matching design spec.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 11: Frontend JavaScript Core

**Files:**
- Create: `static/app.js`

**Step 1: Write JavaScript core functions**

Create `static/app.js`:

```javascript
// Global state
let currentPRs = [];
let currentPage = 1;
let totalResults = 0;

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    await init();
});

async function init() {
    // Check auth status
    const authStatus = await checkAuth();
    if (!authStatus.authenticated) {
        showAuthBanner(authStatus.error);
    }

    // Load default query
    const defaultQuery = await fetch('/api/default-query').then(r => r.json());
    document.getElementById('search-input').value = defaultQuery.query;

    // Auto-execute search
    await executeSearch(defaultQuery.query);

    // Set up event listeners
    document.getElementById('search-btn').addEventListener('click', () => {
        const query = document.getElementById('search-input').value;
        executeSearch(query);
    });

    document.getElementById('search-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            const query = document.getElementById('search-input').value;
            executeSearch(query);
        }
    });

    document.getElementById('refresh-btn').addEventListener('click', () => {
        const query = document.getElementById('search-input').value;
        currentPage = 1;
        document.getElementById('pr-cards-container').innerHTML = '';
        executeSearch(query);
    });
}

async function checkAuth() {
    const response = await fetch('/api/auth/status');
    return await response.json();
}

function showAuthBanner(message) {
    const banner = document.getElementById('auth-banner');
    banner.textContent = '⚠️ ' + message;
    banner.classList.remove('hidden');
}

async function executeSearch(query) {
    showLoading('Searching PRs...');

    const response = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, page: currentPage, per_page: 10 })
    });

    const data = await response.json();

    if (data.error) {
        showToast(data.error, 'error');
        hideLoading();
        return;
    }

    currentPRs = data.prs;
    totalResults = data.total;

    hideLoading();
    renderPRCards(data.prs);
}

function renderPRCards(prs) {
    const container = document.getElementById('pr-cards-container');

    if (prs.length === 0) {
        container.innerHTML = '<div class="loading">No PRs found</div>';
        return;
    }

    prs.forEach(pr => {
        const card = createPRCard(pr);
        container.appendChild(card);

        // Fetch job data in background
        loadPRJobs(pr.owner, pr.repo, pr.number, card);
    });
}

function createPRCard(pr) {
    const card = document.createElement('div');
    card.className = 'pr-card';
    card.id = `pr-${pr.owner}-${pr.repo}-${pr.number}`;

    const age = getAge(pr.created_at);

    card.innerHTML = `
        <div class="pr-header">
            <div class="pr-title">
                PR #${pr.number} - ${pr.title}
            </div>
            <div class="pr-meta">
                ${pr.owner}/${pr.repo} • ${pr.author} • ${age}
            </div>
        </div>
        <div class="job-section" id="e2e-${pr.owner}-${pr.repo}-${pr.number}">
            <div class="job-section-header">▶ E2E Jobs (loading...)</div>
            <div class="job-list"></div>
        </div>
        <div class="job-section" id="payload-${pr.owner}-${pr.repo}-${pr.number}">
            <div class="job-section-header">▶ Payload Jobs (loading...)</div>
            <div class="job-list"></div>
        </div>
    `;

    return card;
}

async function loadPRJobs(owner, repo, number, cardElement) {
    try {
        const response = await fetch(`/api/pr/${owner}/${repo}/${number}`);
        const data = await response.json();

        updateCardWithJobs(cardElement, data, owner, repo, number);
    } catch (error) {
        showCardError(cardElement, error.message);
    }
}

function updateCardWithJobs(cardElement, data, owner, repo, number) {
    // Update E2E section
    const e2eSection = cardElement.querySelector(`#e2e-${owner}-${repo}-${number}`);
    const e2eHeader = e2eSection.querySelector('.job-section-header');
    const e2eList = e2eSection.querySelector('.job-list');

    const e2eFailed = data.e2e.failed || [];
    const e2eRunning = data.e2e.running || [];

    e2eHeader.textContent = `▶ E2E Jobs (${e2eFailed.length} failed | ${e2eRunning.length} running)`;
    e2eHeader.onclick = () => e2eList.classList.toggle('expanded');

    if (e2eFailed.length > 0) {
        e2eList.innerHTML = e2eFailed.map(job => `
            <div class="job-item">
                <div class="job-name">❌ ${job.name} (${job.consecutive} consecutive)</div>
                <div class="job-actions">
                    <button class="btn" onclick="retestJob('${owner}', '${repo}', ${number}, ['${job.name}'], 'e2e')">Retest</button>
                    <button class="btn btn-secondary" disabled>Analyze</button>
                </div>
            </div>
        `).join('');

        e2eList.innerHTML += `<button class="btn" onclick="retestAllE2E('${owner}', '${repo}', ${number})">Retest All E2E</button>`;
    } else {
        e2eList.innerHTML = '<div style="padding: 0.5rem;">✅ No failed jobs</div>';
    }

    // Update Payload section
    const payloadSection = cardElement.querySelector(`#payload-${owner}-${repo}-${number}`);
    const payloadHeader = payloadSection.querySelector('.job-section-header');
    const payloadList = payloadSection.querySelector('.job-list');

    const payloadFailed = data.payload.failed || [];
    const payloadRunning = data.payload.running || [];

    payloadHeader.textContent = `▶ Payload Jobs (${payloadFailed.length} failed | ${payloadRunning.length} running)`;
    payloadHeader.onclick = () => payloadList.classList.toggle('expanded');

    if (payloadFailed.length > 0) {
        payloadList.innerHTML = payloadFailed.map(job => `
            <div class="job-item">
                <div class="job-name">❌ ${job.name} (${job.consecutive} consecutive)</div>
                <div class="job-actions">
                    <button class="btn" onclick="retestJob('${owner}', '${repo}', ${number}, ['${job.name}'], 'payload')">Retest</button>
                    <button class="btn btn-secondary" disabled>Analyze</button>
                </div>
            </div>
        `).join('');

        payloadList.innerHTML += `<button class="btn" onclick="retestAllPayload('${owner}', '${repo}', ${number})">Retest All Payload</button>`;
    } else {
        payloadList.innerHTML = '<div style="padding: 0.5rem;">✅ No failed jobs</div>';
    }
}

async function retestJob(owner, repo, pr, jobs, type) {
    const response = await fetch('/api/retest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ owner, repo, pr, jobs, type })
    });

    const result = await response.json();

    if (result.error === 'auth_failed') {
        showAuthBanner('GitHub CLI not authenticated. Run: gh auth login');
        disableAllRetestButtons();
    } else if (result.success) {
        showToast(`✅ Retest triggered for ${jobs.length} job(s)`, 'success');
    } else {
        showToast(`❌ Error: ${result.error}`, 'error');
    }
}

function retestAllE2E(owner, repo, pr) {
    const card = document.getElementById(`pr-${owner}-${repo}-${pr}`);
    const e2eSection = card.querySelector(`#e2e-${owner}-${repo}-${pr}`);
    const jobItems = e2eSection.querySelectorAll('.job-item');

    const jobs = Array.from(jobItems).map(item => {
        const nameElement = item.querySelector('.job-name');
        const match = nameElement.textContent.match(/❌ (.+?) \(/);
        return match ? match[1] : null;
    }).filter(Boolean);

    retestJob(owner, repo, pr, jobs, 'e2e');
}

function retestAllPayload(owner, repo, pr) {
    const card = document.getElementById(`pr-${owner}-${repo}-${pr}`);
    const payloadSection = card.querySelector(`#payload-${owner}-${repo}-${pr}`);
    const jobItems = payloadSection.querySelectorAll('.job-item');

    const jobs = Array.from(jobItems).map(item => {
        const nameElement = item.querySelector('.job-name');
        const match = nameElement.textContent.match(/❌ (.+?) \(/);
        return match ? match[1] : null;
    }).filter(Boolean);

    retestJob(owner, repo, pr, jobs, 'payload');
}

function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 5000);
}

function showLoading(message) {
    const container = document.getElementById('pr-cards-container');
    container.innerHTML = `<div class="loading">${message}</div>`;
}

function hideLoading() {
    const loading = document.querySelector('.loading');
    if (loading && loading.textContent.includes('Searching')) {
        loading.remove();
    }
}

function showCardError(cardElement, message) {
    cardElement.innerHTML += `<div style="color: var(--primary); padding: 1rem;">⚠️ Error: ${message}</div>`;
}

function disableAllRetestButtons() {
    document.querySelectorAll('button').forEach(btn => {
        if (btn.textContent.includes('Retest')) {
            btn.disabled = true;
        }
    });
}

function getAge(createdAt) {
    const created = new Date(createdAt);
    const now = new Date();
    const diffMs = now - created;
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return 'today';
    if (diffDays === 1) return '1 day old';
    return `${diffDays} days old`;
}
```

**Step 2: Commit**

```bash
git add static/app.js
git commit -m "feat: add frontend JavaScript with PR card rendering

Implements search, progressive PR card loading, expandable job sections,
retest functionality, toast notifications. Full interactive dashboard.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 12: Final Testing & README

**Files:**
- Modify: `README.md`

**Step 1: Update README with usage instructions**

Replace `README.md`:

```markdown
# PR CI Dashboard

Dashboard to see PR job failures and retest them.

## Features

- Search PRs using GitHub query syntax
- View failed e2e and payload jobs with consecutive failure counts
- One-click retest via local \`gh\` CLI
- Progressive loading for fast UX
- Grayed-out "Analyze" button (future enhancement)

## Prerequisites

1. **Python 3.8+**
2. **GitHub CLI** - Install from https://cli.github.com
3. **GitHub CLI authenticated** - Run: \`gh auth login\`

## Installation

```bash
# Clone repository
cd /home/jamoluhrsen/repos/RedHat/workbits/pr-ci-dashboard

# Install dependencies
pip install -r requirements.txt
```

## Usage

Start the dashboard with optional search parameters:

```bash
# Basic usage (default search)
python server.py

# Add custom search parameters
python server.py --author:jluhrsen --repo:openshift/ovn-kubernetes

# Multiple parameters
python server.py --author:jluhrsen --label:bug is:draft
```

Then visit: **http://localhost:5000**

### Default Search

The dashboard defaults to:
```
is:pr is:open archived:false author:openshift-pr-manager[bot]
```

CLI arguments are appended to this base query.

## How It Works

1. **Server starts** → Fetches bash scripts from GitHub PR #177
2. **User searches** → GitHub CLI finds matching PRs
3. **For each PR** → Executes e2e-retest.sh and payload-retest.sh
4. **Parses output** → Extracts failed/running jobs
5. **User clicks Retest** → Posts `/test` or `/payload-job` comment via `gh`

## Architecture

- **Backend**: Flask server with subprocess execution
- **Frontend**: Vanilla HTML/CSS/JavaScript (Red Hat theme)
- **Scripts**: Fetched from https://github.com/openshift-eng/ai-helpers/pull/177
- **Auth**: Local `gh` CLI (no OAuth needed)

## Project Structure

```
pr-ci-dashboard/
├── server.py                 # Flask app entry point
├── api/
│   ├── search.py            # PR search via gh
│   ├── jobs.py              # Job status fetching
│   └── retest.py            # Post retest comments
├── parsers/
│   ├── e2e_parser.py        # Parse e2e-retest.sh output
│   └── payload_parser.py    # Parse payload-retest.sh output
├── utils/
│   ├── script_fetcher.py    # Fetch scripts from GitHub
│   ├── job_executor.py      # Execute bash scripts
│   └── gh_auth.py           # Check gh CLI auth
├── static/
│   └── app.js               # Frontend JavaScript
├── templates/
│   └── index.html           # Dashboard HTML
└── docs/
    └── design.md            # Design document
```

## Troubleshooting

**"GitHub CLI not authenticated"**
```bash
gh auth login
```

**"Failed to fetch scripts"**
- Check internet connection
- Verify GitHub is accessible
- Check PR #177 still exists

**Scripts timeout**
- Increase timeout in `utils/job_executor.py` (default 30s)
- PRs with many jobs may take longer

**Retest button disabled**
- Check `gh auth status` in terminal
- Re-authenticate if needed

## Future Enhancements

- "Analyze" button - AI-powered failure pattern detection
- Auto-refresh toggle
- URL state persistence
- Deployment to Red Hat internal platform

## License

See LICENSE for details.
```

**Step 2: Test full workflow**

1. Start server:
```bash
python server.py --author:jluhrsen
```

2. Visit http://localhost:5000
3. Verify search box has default query + CLI args
4. Search executes automatically
5. PR cards appear with loading spinners
6. Job data loads progressively
7. Click to expand e2e/payload sections
8. Test retest button (optional - posts real comment)

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add complete README with usage instructions

Includes installation, usage, architecture overview, troubleshooting.
Ready for users to clone and run locally.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Success Criteria

- ✅ Scripts fetch from GitHub on startup
- ✅ Dashboard loads with pre-filled search
- ✅ Search finds PRs via gh CLI
- ✅ PR cards show e2e/payload job status
- ✅ Consecutive failure counts display
- ✅ Running jobs shown separately
- ✅ Retest buttons post comments via gh
- ✅ Auth errors handled gracefully
- ✅ "Analyze" button grayed out
- ✅ README complete

## Next Steps

After MVP completion:
1. Test with real PRs
2. Gather feedback from team
3. Implement "Analyze" button
4. Consider deployment to shared platform
