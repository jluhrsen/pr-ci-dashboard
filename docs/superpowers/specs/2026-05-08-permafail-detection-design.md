# Permafail Detection & Auto-Retest Design

**Status:** Work in Progress (Brainstorming Phase)
**Date:** 2026-05-08
**Goal:** Add intelligent permafail detection and automated retesting to Flake Buster dashboard

## Overview

Enhance the PR CI Dashboard to:
1. **Detect permafails** - Identify when job failures are systematic/permanent vs. flaky
2. **Provide clear visual indicators** - Show users which jobs are stuck vs. worth retesting
3. **Automate smart retesting** - Retry flaky failures automatically, skip permafails

## Key Decisions

### Permafail Detection Logic

A job is considered a **permafail** when consecutive failures show:
- **Same test case** fails in every run (by job type)
- **Same infrastructure issue** in every run (e.g., "network operator never ready", cloud provider issues, cluster install failures with identical root cause)

### AI Integration Approach

- Dashboard runs as local Flask server
- Shells out to local Claude Code CLI for analysis
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
  - job_url (primary key)          -- Unique identifier from Prow
  - pr_number
  - repo
  - job_name
  - analysis_result                 -- JSON: {permafail: bool, reason: str, test_names: [], ...}
  - analyzed_at                     -- Timestamp
  - override                        -- User manually cleared permafail flag
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
- `utils/db.py` - SQLite connection, schema, queries
- `utils/ai_analyzer.py` - Shell out to Claude Code CLI, parse results
- `api/analysis.py` - Analysis endpoint handlers

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

### AI Analysis Integration

**Claude Code CLI invocation:**
```bash
claude-code --non-interactive "Analyze these Prow job failures: [URLs].
Determine if this is a permafail (same failure pattern across all runs).
Output JSON: {permafail: bool, reason: str, failing_tests: []}"
```

**Analysis flow:**
1. Check SQLite for cached result by job URL
2. If not cached:
   - Invoke Claude Code CLI with job URLs
   - Parse JSON response
   - Store in SQLite
3. Return result to frontend

**AI analysis criteria:**
- Compare test failure patterns across runs
- Identify infrastructure vs. test failures
- Detect identical error messages/stack traces
- Flag if same test case fails in 100% of runs

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

## Open Questions / Next Steps

### Still to decide:

1. **AI analysis details:**
   - Which /ci skills to wrap? (Need to explore available skills)
   - How to structure the analysis prompt?
   - How deep to analyze (recent 2-3 runs vs all consecutive failures)?

2. **Auto-retest timing:**
   - Immediate retry or wait interval after 1st/2nd failure?
   - Backoff strategy if multiple retests needed?

3. **Error handling:**
   - What if AI analysis fails/times out?
   - Fallback behavior?
   - Show error state or fail silently?

4. **Scope boundaries:**
   - Permafail detection only for e2e jobs, or payload jobs too?
   - Different thresholds for different job types?

5. **Future enhancements:**
   - Notification when permafail detected?
   - Team-wide permafail tracking (when moved to server)?
   - Integration with JIRA for bug filing?

### Implementation approach:

Once design is finalized:
1. Create implementation plan (writing-plans skill)
2. Phase 1: SQLite schema + basic CRUD
3. Phase 2: AI analyzer integration
4. Phase 3: Frontend UI (dumpster fire, context menu)
5. Phase 4: Auto-retest logic enhancement
6. Phase 5: Testing & refinement

## Notes

- Visual companion used for icon design exploration
- Cartoon dumpster fire chosen for fun, clear communication
- Context menu chosen for extensibility
- SQLite chosen for scale (10s of users, 100s-1000s of jobs)
- Manual "Check for permafail" keeps AI compute controlled
