# PR CI Dashboard - Design Document

**Date**: 2026-02-10
**Author**: James (Jamo) Luhrsen
**Status**: Approved for Implementation

---

## Overview & Goals

### Problem

OpenShift PRs run numerous e2e and payload CI jobs that frequently need retesting due to instability. Currently, developers must manually run bash scripts (`e2e-retest.sh`, `payload-retest.sh`) for each PR they're monitoring, parse terminal output, and decide which jobs to retest. This workflow doesn't scale when tracking multiple PRs across different repositories.

### Solution

A local web dashboard that aggregates PR status from GitHub searches, displays failed/running jobs with consecutive failure counts, and provides one-click retest functionality. The dashboard fetches bash scripts from the ai-helpers repository and leverages the local `gh` CLI for authentication, keeping the implementation simple and maintainable.

### Key Features

- GitHub search query interface (search state in input box only)
- Real-time job status for e2e and payload jobs
- Consecutive failure tracking to identify alarming patterns
- One-click retest (individual jobs or batch "retest all")
- Progressive loading for responsive UX with multiple PRs
- Future AI analysis to detect repeated test failures vs random flakes

### Non-Goals (MVP)

- Real-time updates (manual refresh button only)
- Job log viewing (links to external prow/payload dashboards)
- Historical trend analysis
- Multi-user session management
- URL state persistence (search query not in URL)

---

## Architecture

### Technology Stack

**Backend:**
- **Flask** - Lightweight Python web framework
- **Subprocess** - Call bash scripts fetched from GitHub
- **Requests** - Fetch scripts from GitHub raw URLs
- **GitHub CLI (`gh`)** - Authentication and API calls (reuses local credentials)
- **Session storage** - Track user state (search history, pagination)

**Frontend:**
- **Vanilla HTML/CSS/JavaScript** - No frameworks
- **Fetch API** - Async data loading from backend
- **Red Hat theme** - Dark theme with `--primary: #ee0000`

**No database needed** - All data fetched fresh from GitHub/scripts on demand

### Script Dependency Management

**The dashboard fetches bash scripts from GitHub at startup:**

```python
# Script URLs (PR branch - will change to 'main' after merge)
E2E_SCRIPT_URL = "https://raw.githubusercontent.com/openshift-eng/ai-helpers/add-ci-pr-retest-command/plugins/ci/skills/e2e-retest/e2e-retest.sh"
PAYLOAD_SCRIPT_URL = "https://raw.githubusercontent.com/openshift-eng/ai-helpers/add-ci-pr-retest-command/plugins/ci/skills/payload-retest/payload-retest.sh"

def fetch_scripts_on_startup():
    """Download scripts from GitHub to /tmp on server startup"""
    import requests

    scripts = {
        '/tmp/e2e-retest.sh': E2E_SCRIPT_URL,
        '/tmp/payload-retest.sh': PAYLOAD_SCRIPT_URL
    }

    for local_path, url in scripts.items():
        response = requests.get(url)
        response.raise_for_status()

        with open(local_path, 'w') as f:
            f.write(response.text)

        os.chmod(local_path, 0o755)  # Make executable

    print("✅ Scripts fetched from GitHub")
```

**Benefits:**
- No git submodules or local clones needed
- Always uses latest scripts from ai-helpers repo
- Simple dependency management

**Considerations:**
- Requires network connection on startup
- Breaks if GitHub is down (could add fallback to cached copies)
- After PR merge, update URLs to use `main` branch instead of `add-ci-pr-retest-command`

### Deployment Model

**Local development server:**
```bash
cd /home/jamoluhrsen/repos/RedHat/workbits/pr-ci-dashboard
python server.py --author:jluhrsen --repo:openshift/ovn-kubernetes
```

- Runs on `http://localhost:5000`
- CLI flags append to default search query: `is:pr is:open archived:false author:openshift-pr-manager[bot]`
- Single-user (developer's local machine)
- Future: Can deploy to Red Hat internal platform with minimal changes

### Authentication Flow

**Simplified `gh` CLI approach:**

1. **Server startup**: Run `gh auth status` to check authentication
   - Success → Enable retest buttons
   - Failure → Show warning banner: "⚠️ GitHub CLI not authenticated. Run `gh auth login` to enable retest."

2. **On retest click**: Execute `gh pr comment <pr> --repo <repo> --body "/test <job>"`
   - Success → Show toast "✅ Retest triggered"
   - Auth error → Show banner + disable retest buttons
   - Other error → Show error toast

**No OAuth implementation needed** - Reuses developer's existing `gh` authentication

---

## User Interface Design

### Page Layout

**Fixed header:**
```
┌─────────────────────────────────────────────────────────┐
│ PR CI Dashboard                    [Refresh] [Settings] │
├─────────────────────────────────────────────────────────┤
│ Search: [is:pr is:open archived:false author:jluhrsen...] │
│         [Search]                                         │
└─────────────────────────────────────────────────────────┘
```

**Scrolling content area:**
```
┌─────────────────────────────────────────────────────────┐
│ ⚠️ GitHub CLI not authenticated. Run gh auth login...   │ ← Banner (if auth fails)
├─────────────────────────────────────────────────────────┤
│                                                          │
│ ┌──────────────────────────────────────────────────┐    │
│ │ PR #2838 • openshift/ovn-kubernetes • 3 days old │    │
│ │ Fix networking bug in cluster setup              │    │
│ │                                                   │    │
│ │ ▶ E2E Jobs (3 failed | 2 running)               │    │ ← Collapsed
│ └──────────────────────────────────────────────────┘    │
│                                                          │
│ ┌──────────────────────────────────────────────────┐    │
│ │ PR #2801 • openshift/ovn-kubernetes • 5 days old │    │
│ │ Update controller logic for pod networking       │    │
│ │                                                   │    │
│ │ ▼ E2E Jobs (2 failed | 0 running)               │    │ ← Expanded
│ │   ❌ e2e-aws-ovn (5 consecutive)                │    │
│ │      [Retest] [Analyze]                          │    │
│ │   ❌ e2e-gcp-ovn (2 consecutive)                │    │
│ │      [Retest] [Analyze]                          │    │
│ │   [Retest All E2E]                               │    │
│ │                                                   │    │
│ │ ▼ Payload Jobs (1 failed | 1 running)           │    │
│ │   ❌ periodic-ci-...-aws (3 consecutive)        │    │
│ │      [Retest] [Analyze]                          │    │
│ │   [Retest All Payload]                           │    │
│ └──────────────────────────────────────────────────┘    │
│                                                          │
│ [Load More (10 of 47 results)]                          │
└─────────────────────────────────────────────────────────┘
```

### PR Card Design

**Card header** (always visible):
- PR number, repository, age
- PR title
- Collapsed job summary: "▶ E2E Jobs (3 failed | 2 running)"

**Expanded section** (click to toggle):
- List of failed jobs with consecutive count
- Individual [Retest] and [Analyze] buttons per job
- [Retest All] button at bottom of section
- Running jobs shown separately (no action buttons)

**Visual states:**
- **Loading skeleton**: Card header visible, job sections show spinner
- **Loaded with data**: Expandable sections populated with jobs
- **Error state**: "⚠️ Failed to load job data [Retry]"
- **Empty state**: "✅ No failed e2e jobs" / "✅ No failed payload jobs"

### Visual Design

- Red Hat theme colors (--primary: #ee0000, dark background)
- PR cards with expandable sections
- Clean monospace fonts for job names
- Toast notifications
- No Red Hat logo (text header only)

---

## Data Flow & API Design

### Backend API Endpoints

```
GET  /                                    → Serve index.html
GET  /static/<path>                       → Serve CSS/JS assets
GET  /api/auth/status                     → Check gh CLI auth status
POST /api/search                          → Search PRs via GitHub API
     Body: { "query": "is:pr is:open..." }
     Returns: { "prs": [...], "total": 47 }

GET  /api/pr/<owner>/<repo>/<number>      → Get job data for one PR
     Returns: {
       "pr": {...},
       "e2e": { "failed": [...], "running": [...] },
       "payload": { "failed": [...], "running": [...] }
     }

POST /api/retest                          → Post retest comment
     Body: {
       "owner": "openshift",
       "repo": "ovn-kubernetes",
       "pr": 2838,
       "jobs": ["e2e-aws-ovn", "e2e-gcp-ovn"],
       "type": "e2e"  // or "payload"
     }
     Returns: { "success": true } or { "error": "auth failed" }
```

### Data Loading Flow

**Initial page load:**

1. Frontend loads with pre-filled search query (from CLI args)
2. Auto-submit search → `POST /api/search`
3. Backend calls GitHub Search API → returns first 10 PRs
4. Frontend renders 10 empty PR cards with loading spinners
5. Frontend spawns 10 parallel requests: `GET /api/pr/<owner>/<repo>/<number>`
6. Backend for each PR:
   - Spawns `echo "4" | bash /tmp/e2e-retest.sh <repo> <pr>` (exit option 4: "Just show list")
   - Spawns `echo "3" | bash /tmp/payload-retest.sh <repo> <pr>` (exit option 3: "Just show list")
   - Parses text output for job names, consecutive counts, running counts
   - Returns structured JSON
7. Frontend updates each card as responses arrive (streaming/progressive rendering)

**"Load More" click:**
- Fetch next 10 PRs from search results
- Repeat steps 4-7
- New cards append to bottom

### Bash Script Output Parsing

**Parse `e2e-retest.sh` output:**

Expected format (from actual script output):
```
Failed e2e jobs:
  ❌ e2e-aws-ovn
     Consecutive failures: 5
     Recent history: 8 fail / 2 pass / 0 abort
⏳ Currently running (2 jobs):
  • e2e-metal-ipi
```

Python parser:
```python
import re

def parse_e2e_output(output: str) -> dict:
    failed_jobs = []
    running_jobs = []

    # Extract failed jobs with consecutive count
    # Pattern: ❌ <job-name>\n     Consecutive failures: <num>
    failed_pattern = r'❌ (.+?)\n\s+Consecutive failures: (\d+)'
    for match in re.finditer(failed_pattern, output):
        job_name = match.group(1).strip()
        consecutive = int(match.group(2))
        failed_jobs.append({"name": job_name, "consecutive": consecutive})

    # Extract running jobs
    # Pattern: • <job-name>
    running_pattern = r'• (.+?)(?:\n|$)'
    running_section = re.search(r'Currently running.*?:\n(.*?)(?:\n\n|$)', output, re.DOTALL)
    if running_section:
        for match in re.finditer(running_pattern, running_section.group(1)):
            running_jobs.append(match.group(1).strip())

    return {"failed": failed_jobs, "running": running_jobs}
```

**Similar parser for `payload-retest.sh` output**

---

## Implementation Details

### Script Execution Strategy

**Calling bash scripts from Python:**

```python
import subprocess
from concurrent.futures import ThreadPoolExecutor

def get_e2e_jobs(repo: str, pr_number: int) -> dict:
    """Execute e2e-retest.sh and parse output"""
    script_path = "/tmp/e2e-retest.sh"

    # Pipe "4" to select "Just show list (done)"
    result = subprocess.run(
        ["bash", script_path, repo, str(pr_number)],
        input="4\n",
        capture_output=True,
        text=True,
        timeout=30
    )

    if result.returncode != 0:
        return {"error": "Script failed", "stderr": result.stderr}

    return parse_e2e_output(result.stdout)

def get_payload_jobs(repo: str, pr_number: int) -> dict:
    """Execute payload-retest.sh and parse output"""
    script_path = "/tmp/payload-retest.sh"

    # Pipe "3" to select "Just show list (done)"
    result = subprocess.run(
        ["bash", script_path, repo, str(pr_number)],
        input="3\n",
        capture_output=True,
        text=True,
        timeout=30
    )

    if result.returncode != 0:
        return {"error": "Script failed", "stderr": result.stderr}

    return parse_payload_output(result.stdout)
```

**Parallel execution with limits:**
- Use `ThreadPoolExecutor` with max 10 workers
- Prevents launching hundreds of scripts simultaneously
- Each PR's e2e/payload scripts run in parallel (2 scripts per PR)

### Retest Comment Generation

**E2E retest:**
```python
def retest_e2e_jobs(owner: str, repo: str, pr: int, jobs: list) -> dict:
    """Post /test comments via gh CLI"""
    comment_body = "\n".join([f"/test {job}" for job in jobs])

    result = subprocess.run(
        ["gh", "pr", "comment", str(pr),
         "--repo", f"{owner}/{repo}",
         "--body", comment_body],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        # Check if auth error
        if "authentication" in result.stderr.lower():
            return {"error": "auth_failed"}
        return {"error": result.stderr}

    return {"success": True}
```

**Payload retest:**
```python
def retest_payload_jobs(owner: str, repo: str, pr: int, jobs: list) -> dict:
    """Post /payload-job comments via gh CLI"""
    comment_body = "\n".join([f"/payload-job {job}" for job in jobs])

    result = subprocess.run(
        ["gh", "pr", "comment", str(pr),
         "--repo", f"{owner}/{repo}",
         "--body", comment_body],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        if "authentication" in result.stderr.lower():
            return {"error": "auth_failed"}
        return {"error": result.stderr}

    return {"success": True}
```

### Frontend Implementation

**HTML Structure:**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PR CI Dashboard</title>
    <style>
        /* Red Hat theme */
        :root {
            --primary: #ee0000;
            --primary-dark: #c00000;
            --bg-dark: #0f0f0f;
            --bg-card: #1a1a1a;
            --text-primary: #f5f5f5;
            --border: #3d3d3d;
        }
        /* ... additional styles ... */
    </style>
</head>
<body>
    <div class="navbar">
        <div class="navbar-content">
            <h1>PR CI Dashboard</h1>
            <div class="navbar-actions">
                <button id="refresh-btn">Refresh</button>
            </div>
        </div>
    </div>

    <div id="auth-banner" class="banner hidden">
        ⚠️ GitHub CLI not authenticated. Run <code>gh auth login</code> to enable retest.
    </div>

    <div class="container">
        <div class="search-box">
            <input type="text" id="search-input" placeholder="GitHub search query...">
            <button id="search-btn">Search</button>
        </div>

        <div id="pr-cards-container"></div>

        <button id="load-more-btn" class="hidden">Load More (10 of <span id="total-count"></span>)</button>
    </div>

    <div id="toast-container"></div>

    <script src="/static/app.js"></script>
</body>
</html>
```

**JavaScript core functions:**

```javascript
// On page load
async function init() {
    const authStatus = await checkAuth();
    if (!authStatus.authenticated) {
        showAuthBanner();
        disableRetestButtons();
    }

    const query = getDefaultQuery(); // From server-side CLI args
    document.getElementById('search-input').value = query;

    await executeSearch(query);
}

// Search PRs
async function executeSearch(query) {
    const response = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query })
    });

    const data = await response.json();
    renderPRCards(data.prs);
}

// Render PR cards with loading state
function renderPRCards(prs) {
    const container = document.getElementById('pr-cards-container');

    prs.forEach(pr => {
        const card = createPRCard(pr);
        container.appendChild(card);

        // Fetch job data in background (progressive loading)
        loadPRJobs(pr.owner, pr.repo, pr.number, card);
    });
}

// Load job data for single PR
async function loadPRJobs(owner, repo, number, cardElement) {
    try {
        const response = await fetch(`/api/pr/${owner}/${repo}/${number}`);
        const data = await response.json();

        updateCardWithJobs(cardElement, data);
    } catch (error) {
        showCardError(cardElement, error);
    }
}

// Retest handler
async function retestJob(owner, repo, pr, jobs, type) {
    const response = await fetch('/api/retest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ owner, repo, pr, jobs, type })
    });

    const result = await response.json();

    if (result.error === 'auth_failed') {
        showAuthBanner();
        disableAllRetestButtons();
    } else if (result.success) {
        showToast(`✅ Retest triggered for ${jobs.length} job(s)`);
    } else {
        showToast(`❌ Error: ${result.error}`, 'error');
    }
}
```

**Progressive card rendering:**
- Cards render as API responses arrive (not waiting for all 10)
- Each card has 4 states: loading, loaded, error, empty

---

## User Interactions & Flows

### Search Flow

1. **User arrives at `http://localhost:5000`**
   - Search bar pre-filled with: `is:pr is:open archived:false author:jluhrsen` (from CLI args)
   - Search auto-executes on page load
   - First 10 PRs appear with loading spinners

2. **User modifies search**
   - Types in search bar: `repo:openshift/ovn-kubernetes is:pr is:open`
   - Clicks "Search" or presses Enter
   - Results refresh (no URL update)

3. **User shares setup**
   - Shares command line: `python server.py --author:jluhrsen --repo:openshift/ovn-kubernetes`
   - Colleague runs command → gets same pre-filled search

### Retest Flow

1. **User expands E2E section on PR card**
   - Sees: `❌ e2e-aws-ovn (5 consecutive) [Retest] [Analyze]`

2. **User clicks [Retest]**
   - Immediate action (no confirmation)
   - Toast appears: "✅ Retest triggered for e2e-aws-ovn"
   - Backend posts: `/test e2e-aws-ovn` comment to PR

3. **User clicks [Retest All E2E]**
   - Toast: "✅ Retest triggered for 3 jobs"
   - Backend posts single comment with all `/test` commands

4. **If auth fails during retest**
   - Toast: "❌ Error: Authentication failed"
   - Banner appears: "⚠️ GitHub CLI not authenticated..."
   - All [Retest] buttons become disabled/grayed out

### Refresh Flow

1. **User clicks [Refresh] button**
   - Clear all current PR cards from screen
   - Re-execute search query from scratch
   - Render fresh first 10 PRs with loading spinners
   - Pagination resets to page 1
   - (Search results may have changed - PRs merged/closed/new ones opened)

2. **User clicks [Load More]**
   - Fetches next 10 PRs from search results
   - New cards append to bottom with loading state
   - Button updates: "Load More (20 of 47)"

---

## Error Handling

### Error Scenarios & Responses

**Script fetch errors (on startup):**
- **Network error**: Show error "❌ Failed to fetch scripts from GitHub. Check network connection."
- **GitHub down**: Show error "❌ GitHub unavailable. Cannot start dashboard."
- **404 on script URL**: Show error "❌ Scripts not found. Check PR branch/URLs."

**GitHub API errors:**
- **Rate limit exceeded**: Show toast "⚠️ GitHub API rate limit reached. Try again in X minutes."
- **Network error**: Show toast "❌ Network error. Check connection and try again."
- **Invalid search query**: Show toast "❌ Invalid search query. Check syntax."

**Bash script errors:**
- **Script timeout (>30s)**: Show on card "⚠️ Timed out loading job data [Retry]"
- **Script execution failure**: Show on card "⚠️ Failed to load job data [Retry]"
- **Parse error**: Log to console, show "⚠️ Failed to parse job data [Retry]"

**Auth errors:**
- **gh CLI not installed**: Show banner "❌ GitHub CLI not found. Install from https://cli.github.com"
- **gh CLI not authenticated**: Show banner "⚠️ GitHub CLI not authenticated. Run `gh auth login`"
- **Auth expires during session**: Disable retest buttons + show banner after failed retest

**Per-card [Retry] button:**
- Re-fetches job data for just that PR
- Useful if one script failed/timed out while others succeeded

### Logging Strategy

**Console logging (development):**
- Log all API requests/responses
- Log bash script stdout/stderr
- Log parse errors with raw output
- Log script fetch operations

**Future (production):**
- Log to file for debugging
- Error reporting/telemetry

---

## Future Enhancements

### "Analyze" Button - AI-Powered Failure Analysis

**Grayed out in MVP**, but designed for future implementation.

**When user clicks [Analyze] on a job with consecutive failures:**

1. **Fetch test failure data** for each failed run
   - Pull junit XML or test output from prow artifacts
   - Extract list of failed test case names per run

2. **Compare failures across runs**
   - Map test cases across N consecutive failures
   - Calculate overlap percentage

3. **Determine alarm level**
   - **High alarm (red)**: Same test case(s) fail in 100% of runs
     - "🚨 CRITICAL: 3 tests failing consistently - likely real bug"
   - **Medium alarm (yellow)**: Partial overlap (50-99%)
     - "⚠️ WARNING: Some repeated failures detected"
   - **Low alarm (green)**: No overlap (0-49%)
     - "✅ Random failures - likely infrastructure flake"

4. **Display in modal or expanded card section**
   - Show test case names that are repeating
   - Link to prow job logs for investigation

**Implementation approach:**
- New Flask endpoint: `POST /api/analyze-job`
- Call existing prow APIs to fetch test results
- Use Claude API (or local processing) for pattern detection
- Cache results to avoid re-analyzing same job

**Note:** Cluster install failures appear as test case failures, so the analysis will catch both "same tests failing" and "install failing every time" scenarios.

### Other Future Ideas

- **Auto-refresh toggle**: Poll for updates every N seconds
- **PR filtering by team/area**: Quick filters for "my team's repos"
- **Notification system**: Browser notifications when jobs finish
- **Historical trends**: Track job stability over time
- **Bulk operations**: "Retest all failed jobs across all PRs"
- **URL state persistence**: Bookmark different searches
- **Deployment to Red Hat platform**: Move from localhost to shared hosting
- **Fallback to cached scripts**: If GitHub fetch fails, use last successful copy

---

## Project Structure

```
pr-ci-dashboard/
├── server.py                 # Flask application entry point
├── api/
│   ├── __init__.py
│   ├── search.py            # PR search endpoints
│   ├── jobs.py              # Job data endpoints
│   └── retest.py            # Retest endpoints
├── parsers/
│   ├── __init__.py
│   ├── e2e_parser.py        # Parse e2e-retest.sh output
│   └── payload_parser.py    # Parse payload-retest.sh output
├── static/
│   ├── app.js               # Frontend JavaScript
│   └── styles.css           # CSS (optional, can be inline)
├── templates/
│   └── index.html           # Main dashboard page
├── docs/
│   └── design.md            # This design document
├── requirements.txt         # Python dependencies (Flask, requests)
└── README.md               # Setup and usage instructions
```

---

## Success Criteria

### MVP is complete when:

1. ✅ Server fetches scripts from GitHub on startup
2. ✅ Dashboard loads with pre-filled search from CLI args
3. ✅ Search executes and displays first 10 PRs
4. ✅ Each PR card shows e2e and payload job status
5. ✅ Consecutive failure counts display correctly
6. ✅ Running jobs display separately from failed jobs
7. ✅ Individual retest buttons post comments via `gh` CLI
8. ✅ "Retest All" buttons work for both e2e and payload
9. ✅ Auth errors are handled gracefully with banner
10. ✅ "Load More" pagination works
11. ✅ Refresh clears and re-fetches all data
12. ✅ "Analyze" button is present but grayed out
13. ✅ Error states display clearly with retry options

### Future success metrics:

- Dashboard adopted by 5+ team members
- Average time to retest reduced by 50%
- "Analyze" feature identifies real bugs vs flakes with 80%+ accuracy
- Dashboard deployed to shared Red Hat infrastructure

---

## Implementation Timeline (Rough Estimate)

**Phase 1: Core Infrastructure (Week 1)**
- Flask app setup with basic routing
- Script fetching from GitHub
- Bash script execution and output parsing
- GitHub search API integration

**Phase 2: Frontend UI (Week 1-2)**
- HTML/CSS matching Red Hat theme
- PR card rendering with progressive loading
- Expandable sections for e2e/payload jobs

**Phase 3: Retest Functionality (Week 2)**
- `gh` CLI integration
- Retest button handlers
- Auth error handling

**Phase 4: Polish & Testing (Week 2-3)**
- Error handling edge cases
- Toast notifications
- Load More pagination
- Manual testing with real PRs

**Phase 5: Future Enhancements (TBD)**
- "Analyze" button implementation
- Auto-refresh
- Deployment to shared platform

---

## Open Questions

- Should we cache fetched scripts and use cached versions if GitHub fetch fails?
- Should we cache parsed job data to avoid re-parsing on refresh?
- Should "Load More" fetch 10 more, or allow configurable page size?
- Should we add keyboard shortcuts (e.g., 'r' for refresh)?
- Should we track analytics (which repos are searched most)?
- When PR #177 merges, update script URLs to use `main` branch

---

## Conclusion

This dashboard simplifies PR CI monitoring by aggregating job status, highlighting alarming failure patterns, and enabling one-click retests. By fetching bash scripts from the ai-helpers repository and using the local `gh` CLI, the implementation stays lean with minimal dependencies. The design leaves room for future AI-powered analysis while delivering immediate value to developers managing multiple OpenShift PRs.

**Repository:** `/home/jamoluhrsen/repos/RedHat/workbits/pr-ci-dashboard`
**Script Source:** https://github.com/openshift-eng/ai-helpers/pull/177
