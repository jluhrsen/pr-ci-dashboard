# Permafail Detection & Auto-Retest Design

**Status:** Ready for Implementation
**Date:** 2026-05-08 (Updated: 2026-05-12)
**Goal:** Add intelligent permafail detection and automated retesting to Flake Buster dashboard

## Overview

Enhance the PR CI Dashboard to:
1. **Detect permafails** - Identify when job failures are systematic/permanent vs. flaky
2. **Provide clear visual indicators** - Show users which jobs are stuck vs. worth retesting
3. **Automate smart retesting** - Retry flaky failures automatically, skip permafails

## Key Decisions

### Permafail Detection Logic

**Trigger:** Analysis runs when a job reaches 3 consecutive failures.

**Detection criteria:**

A job is considered a **permafail** when at least one of these conditions is true:
- **Test failures:** At least 1 test case fails in all 3 runs
- **Infrastructure failures:** Same infrastructure error in all 3 runs (e.g., "network operator never ready", cluster install failures with identical root cause)

**Future enhancement:** Extend pattern matching to 4/5, 7/10 for longer failure histories.

**Failure signature format:**

Test failures:
```json
{
  "type": "test_failure",
  "tests": ["TestNetworkPolicy/Baseline", "TestPodConnectivity"]
}
```

Infrastructure failures:
```json
{
  "type": "infra_failure",
  "error": "network operator timeout"
}
```

**Comparison logic:**
- All 3 signatures are `type: "test_failure"` → Check for ≥1 common test name
- All 3 signatures are `type: "infra_failure"` → Compare error messages for similarity
- Mixed types → Not a permafail (inconsistent failure pattern)

### AI Integration Approach

**New skill:** `/pr-ci-dashboard:detect-permafail`

This skill wraps the existing `ci-prow-navigation` skill to analyze job failures in parallel.

**How it works:**
1. Skill receives 3 job URLs (consecutive failures)
2. Spawns 3 parallel subagents using Task tool
3. Each subagent invokes `ci-prow-navigation` skill on one URL
4. Each subagent extracts failure signature (structured format)
5. Main skill compares signatures → permafail verdict

**Dashboard integration:**
- Flask backend shells out to Claude Code CLI: `claude-code skill pr-ci-dashboard:detect-permafail`
- Target user: Red Hat developers already using Claude Code locally
- Future: When moved to central server, same architecture works (users connect to server, server uses local Claude)

### Data Persistence

**Storage:** SQLite database
- Handles scale: 10s of users × dozens of PRs × many jobs = thousands of cached analyses
- Fast indexed lookups by job URL
- Concurrent reads for web server
- No database server to manage
- Easy migration to Postgres/MySQL if needed

**Schema:**
```sql
job_analyses:
  - job_url (TEXT PRIMARY KEY)      -- Unique identifier from Prow
  - pr_number (INTEGER)
  - repo (TEXT)
  - job_name (TEXT)
  - signature (TEXT)                -- JSON failure signature: {"type": "test_failure", "tests": [...]}
  - analyzed_at (TIMESTAMP)
  - permafail_result (TEXT)         -- JSON: full skill output {permafail: bool, reason: str, ...}
  - override (BOOLEAN DEFAULT FALSE) -- User manually cleared permafail flag
```

### Visual Design

**Permafail Indicator:** Cartoon dumpster fire icon (SVG)
- Fun, clear "this is broken" signal
- Scales well at different sizes (16px-32px)
- Red Hat dev culture friendly

**Icon SVG:**
```svg
<svg viewBox="0 0 48 48">
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

**UI Behavior:**
- Dumpster fire icon appears next to job name when permafail detected
- Retest button becomes disabled (grayed out)
- Warning banner shows reason: "Permafail: TestNetworkPolicy/Baseline failed in all runs"

**Override Mechanism:**
- Right-click (or long-press) on job card → context menu
- "Clear permafail" option
- Removes dumpster fire, re-enables retest button
- Sets `override=true` in database
- Context menu extensible for future actions

### Workflow

#### Auto-Retest Logic

**Progressive failure handling:**

1. **1st consecutive failure** → Auto-retest immediately (benefit of the doubt)
2. **2nd consecutive failure** → Auto-retest immediately (could still be flaky)
3. **3rd consecutive failure** → **Trigger permafail analysis**
   - If permafail detected:
     - Mark with dumpster fire icon
     - Disable retest button
     - Stop auto-retesting this job
   - If NOT permafail:
     - Auto-retest (unlucky flakes)
     - Continue monitoring

**Manual override:** "Check for permafail" button available on any job (for early analysis if user is suspicious)

#### State Management

**Local state:** Each user session tracks their own decisions
- Works for local instances now
- Works for central server later (per-user sessions)
- No shared state between users

**Cache invalidation:**
- Analysis cached by job run URL (from `get_consecutive_failure_urls()`)
- Re-analyze if job URL not in database
- Check disk before running AI analysis (avoid redundant work)

## Architecture Components

### Backend (Flask)

**New endpoints:**
- `POST /api/jobs/analyze` - Trigger permafail analysis for a job
  - Input: `{pr, repo, job_name, job_urls[]}`
  - Output: `{permafail: bool, reason: str, test_names: []}`
- `POST /api/jobs/override` - Clear permafail flag
  - Input: `{job_url}`
  - Output: `{success: bool}`
- `GET /api/jobs/status` - Get permafail status for jobs
  - Input: `{job_urls[]}`
  - Output: `{job_url: {permafail: bool, reason: str, override: bool}}`

**New modules:**

`utils/db.py` - SQLite database layer:
- `init_db()` - Create tables if not exist
- `get_signature(job_url)` - Retrieve cached signature for a job URL
- `store_analysis(job_url, pr_number, repo, job_name, signature, permafail_result)` - Cache analysis results
- `get_permafail_status(job_urls)` - Batch query for permafail status of multiple job URLs
- `clear_override(job_url)` - Reset override flag to false

`utils/ai_analyzer.py` - Claude Code CLI integration:
- `analyze_permafail(job_urls, job_name, pr_info)` - Main entry point
  - Builds CLI command with escaped JSON parameters
  - Executes: `claude-code skill pr-ci-dashboard:detect-permafail --job-urls='...' --job-name='...' --pr='...'`
  - Parses JSON output from stdout
  - Returns dict: `{permafail: bool, reason: str, signatures: [...], ...}`

`api/analysis.py` - HTTP endpoint handlers:
- Analysis endpoints implementation
- Request validation
- Error handling and logging

### Frontend (JavaScript)

**New features:**
- "Check for permafail" button on job cards (initially hidden, shows on 2+ failures)
- Dumpster fire icon rendering
- Right-click context menu on job cards
- Auto-retest polling logic (enhanced to check permafail status first)

**UI states:**
```
Job Card States:
1. Normal failure: Red border, active Retest button, no icon
2. Analyzing: Spinner, "Analyzing..." text, disabled Retest
3. Permafail detected: Dumpster fire icon, disabled Retest, warning banner
4. Permafail overridden: Normal failure state restored, override tracked
```

### Skill Design: `/pr-ci-dashboard:detect-permafail`

**Purpose:** Analyze 3 consecutive failed job runs to determine if they represent a permafail pattern.

**Inputs:**
```json
{
  "job_urls": [
    "https://prow.ci.openshift.org/view/...",
    "https://prow.ci.openshift.org/view/...",
    "https://prow.ci.openshift.org/view/..."
  ],
  "job_name": "e2e-aws-ovn",
  "pr": "openshift/ovn-kubernetes#1234"
}
```

**Processing:**
1. Spawn 3 parallel subagents (using Task tool)
2. Each subagent:
   - Invokes `ci-prow-navigation` skill on one job URL
   - Extracts failure signature in structured format:
     - For test failures: Parse test case names from e2e step output
     - For infra failures: Extract error message from failed step (e.g., cluster setup, network operator)
   - Returns signature to main skill
3. Main skill compares the 3 signatures:
   - If all `type: "test_failure"` → check for ≥1 common test name (exact string match)
   - If all `type: "infra_failure"` → compare error messages for similarity (exact match or semantic similarity via LLM)
   - If mixed types → not a permafail
4. Generate verdict with reasoning

**Outputs:**
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

## User Workflows

### Workflow 1: Auto-retest with permafail protection

1. User opens dashboard, sees PRs with failed jobs
2. Job fails → Dashboard auto-retests (1st failure)
3. Job fails again → Dashboard auto-retests (2nd failure)
4. Job fails 3rd time → Dashboard triggers AI analysis
5. If permafail:
   - Dumpster fire appears
   - Retest disabled
   - User investigates manually
6. If not permafail:
   - Dashboard auto-retests
   - Continues monitoring

### Workflow 2: Manual permafail check

1. User sees job with 2 consecutive failures
2. User clicks "Check for permafail"
3. Analysis runs (cached if already done)
4. Result displayed immediately

### Workflow 3: Override false positive

1. User sees dumpster fire on a job
2. User investigates, determines it's not actually a permafail
3. Right-click job card → "Clear permafail"
4. Dumpster fire removed, retest re-enabled
5. User can manually retest

## Error Handling & Edge Cases

### Error Scenarios

**1. AI Analysis Fails/Times Out**
```python
if skill_invocation_fails:
    - Log error
    - Return {"permafail": false, "error": "Analysis failed"}
    - UI shows: "Analysis unavailable, manual check needed"
    - Don't block auto-retest (fail open, not closed)
```

**2. Partial Cache Hits**
```python
# Example: 2 of 3 URLs cached
if some_cached and some_not:
    - Only analyze uncached URLs
    - Reconstruct full comparison from mixed sources
    - Store new signatures in DB
```

**3. Mixed Signature Types**
```python
# Run 1: test_failure, Run 2: infra_failure, Run 3: test_failure
if signatures have different types:
    - Return {"permafail": false, "reason": "Inconsistent failure patterns"}
    - Treat as flaky/varied behavior
```

**4. Empty Test Lists**
```python
# No tests extracted (parsing issue?)
if signature.tests is empty:
    - Return {"permafail": false, "error": "Could not extract test data"}
    - Log for debugging
    - Don't block retesting
```

**5. Context Menu on Non-Permafail Jobs**
```javascript
// User right-clicks job without permafail
if (!job.permafail) {
    // Show different menu or no menu
    // Avoid confusion
}
```

**Philosophy:** Fail gracefully. If analysis can't run or gives ambiguous results, default to allowing retest (conservative approach).

## Data Flow

### End-to-End Flow for Permafail Detection

```
1. User loads dashboard
   ↓
2. Frontend fetches PR jobs from existing /api/jobs endpoint
   ↓
3. For each job with 3+ consecutive failures:
   Frontend calls POST /api/jobs/status with job URLs
   ↓
4. Backend checks SQLite cache:
   - All 3 URLs cached → return cached result
   - Some/none cached → continue to step 5
   ↓
5. Backend invokes ai_analyzer.analyze_permafail()
   ↓
6. ai_analyzer shells out to Claude Code CLI:
   $ claude-code skill pr-ci-dashboard:detect-permafail --job-urls="[...]" --job-name="..." --pr="..."
   ↓
7. Skill spawns 3 parallel Task subagents
   Each runs: ci-prow-navigation on one URL
   ↓
8. Subagents return signatures to skill
   ↓
9. Skill compares signatures → verdict
   ↓
10. Skill returns JSON to CLI
    ↓
11. Backend parses JSON, stores in SQLite:
    - Each job URL → signature
    - Overall result → permafail_result
    ↓
12. Backend returns to frontend:
    {job_url: {permafail: bool, reason: str, override: bool}}
    ↓
13. Frontend updates UI:
    - Show dumpster fire if permafail
    - Disable retest button
    - Display reason in warning banner
```

### Auto-Retest Flow

```
1. Job fails (detected via polling)
   ↓
2. consecutiveFailures++
   ↓
3. If consecutiveFailures <= 2:
   → Auto-retest immediately

4. If consecutiveFailures === 3:
   → Trigger analysis (steps 3-13 above)
   → If NOT permafail: auto-retest
   → If permafail: stop, show dumpster fire
```

## Implementation Phases

### Phase 1: Database Foundation
- Create `utils/db.py` with SQLite schema
- Add init script to create tables on first run
- Write basic CRUD functions
- Test with mock data

### Phase 2: Skill Development
- Create `/pr-ci-dashboard:detect-permafail` skill
- Define signature extraction logic from `ci-prow-navigation` output
- Implement parallel subagent spawning
- Test signature comparison logic
- Handle edge cases (mixed types, empty results)

### Phase 3: Backend API
- Implement `utils/ai_analyzer.py` (Claude Code CLI wrapper)
- Create `api/analysis.py` endpoints
- Wire up SQLite caching
- Test full analysis flow end-to-end

### Phase 4: Frontend UI
- Add dumpster fire SVG icon component
- Implement "Check for Permafail" button
- Build context menu (right-click)
- Add permafail state to job cards
- Wire up API calls

### Phase 5: Auto-Retest Integration
- Enhance existing auto-retest polling logic
- Add 3-failure threshold check
- Integrate permafail detection into retry flow
- Test complete workflow

### Phase 6: Polish & Testing
- Error handling refinement
- Performance optimization (parallel requests)
- User testing with real PRs
- Documentation updates

**Estimated scope:** ~2-3 weeks implementation (part-time)

## Scope & Applicability

**Included in v1:**
- E2E jobs and payload jobs (same analysis approach for both)
- 3-failure threshold for all job types
- Manual "Check for permafail" button (user-controlled)
- Auto-retest with permafail protection

**Not included in v1 (future enhancements):**
- Notification system when permafail detected
- Team-wide permafail tracking (when moved to server)
- JIRA integration for bug filing
- Extended pattern matching (4/5, 7/10 thresholds)
- Different thresholds per job type
- Auto-retest timing/backoff configuration

**Auto-retest behavior:**
- 1st and 2nd failures: Immediate retry (no delay)
- Future: Could add backoff strategy if needed

## Notes

- Visual companion used for icon design exploration
- Cartoon dumpster fire (Option C) chosen for fun, clear communication
- Context menu chosen for extensibility
- SQLite chosen for scale (10s of users, 100s-1000s of jobs)
- Manual "Check for permafail" keeps AI compute controlled
- Conservative error handling: fail open (allow retest) rather than closed
- Skill wraps existing `ci-prow-navigation` skill (no new Prow integration needed)
