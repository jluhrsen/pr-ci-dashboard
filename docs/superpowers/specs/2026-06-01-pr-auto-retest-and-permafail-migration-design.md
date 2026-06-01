# PR Auto-Retest and Permafail Migration Design

**Date:** 2026-06-01  
**Stories:** CORENET-7148, CORENET-7149  
**Author:** Claude Code

## Overview

This design covers two related enhancements to the pr-ci-dashboard:

1. **CORENET-7148**: Auto-retest failed jobs in branch sync and downstream merge PRs
2. **CORENET-7149**: Migrate permafail detection to ai-helpers plugin marketplace

## Background

The pr-ci-dashboard currently provides manual retest capabilities for failed OpenShift PR CI jobs. Users must click "Retest" buttons to trigger retests. For automation PRs (branch syncs, downstream merges), this manual intervention becomes a bottleneck.

The permafail detection feature exists as a local skill in `commands/detect-permafail.md`, but should be moved to the ai-helpers plugin marketplace for better discoverability and reusability across projects.

## Goals

### CORENET-7148 Goals
- Enable per-PR auto-retest toggle for automation PRs
- Retest failed jobs automatically on first and second failure
- Check for permafail pattern before retesting on third failure
- Persist toggle state across browser sessions
- Identify branch sync and downstream merge PRs by title patterns

### CORENET-7149 Goals
- Migrate detect-permafail skill to ai-helpers plugin
- Update pr-ci-dashboard to consume from plugin
- Maintain existing functionality and API contract
- Remove local skill files and symlinks

## CORENET-7148: Auto-Retest Implementation

### Architecture

Frontend-only implementation using localStorage and polling. No backend changes required.

**Components:**
1. **Toggle UI** - Checkbox on each PR card
2. **State Management** - localStorage for persistence
3. **Polling Loop** - 30-second intervals for enabled PRs
4. **Retest Logic** - Failure count tracking with permafail check
5. **PR Detection** - Title pattern matching for automation PRs

### PR Detection Logic

Branch sync PRs match: `NO-JIRA: Branch Sync ${SOURCE_BRANCH} to ${TARGET_BRANCH} [${DATE}]`  
Downstream merge PRs match: `NO-JIRA: DownStream Merge [${DATE}]`

The toggle appears on all PRs but is most useful for these automation PRs.

### State Tracking

```javascript
// In-memory state
const autoRetestEnabled = new Map(); // "owner/repo/123" -> boolean
const jobFailureCounters = new Map(); // "owner/repo/123/job-name" -> count
const jobStateCache = new Map(); // "owner/repo/123/job-name" -> 'success'|'failure'|'pending'

// Persisted state
localStorage.setItem('autoRetestEnabled', JSON.stringify({
    "openshift/ovn-kubernetes/1234": true,
    "openshift/cluster-network-operator/5678": false
}));
```

Failure counters are NOT persisted - they reset on page reload. This prevents stale counts from blocking legitimate retests.

### Retest Decision Flow

```
Job state: success -> failure
  |
  v
Increment failure counter for this job
  |
  v
Counter <= 2?
  |-- YES --> Retest immediately
  |
  |-- NO, Counter == 3 --> Check permafail
                              |
                              |-- Not permafail --> Retest
                              |-- Permafail --> Disable auto-retest for this PR
```

### Polling Implementation

- Poll `/api/jobs?pr={number}&repo={repo}` every 30 seconds
- Only poll PRs where toggle is enabled
- Track state transitions (success→failure triggers retest)
- Stop polling when PR tab is closed or toggle disabled

### Error Handling

- Permafail check failure → treat as non-permafail, allow retest
- Retest API failure → show toast notification, don't increment counter
- Network errors → retry next polling cycle

### UI/UX

**Toggle Location:** Checkbox at top of each PR card, before job sections

**Visual States:**
- Unchecked (gray) - Auto-retest disabled
- Checked (blue) - Auto-retest enabled
- Disabled (grayed out) - Permafail detected, auto-retest stopped

**Notifications:**
- Toast on auto-retest trigger: "🔄 Retesting {job-name} (attempt {N})"
- Toast on permafail detection: "⚠️ Permafail detected on {job-name}: {reason}"

## CORENET-7149: Permafail Migration

### Current State

```
pr-ci-dashboard/
├── commands/detect-permafail.md  (768 lines - full implementation)
├── .claude/skills/               (symlink to ~/ai-helpers/.claude/skills/)
└── utils/ai_analyzer.py          (reads local files, invokes Claude CLI)
```

### Target State

```
ai-helpers/
└── plugins/ci/
    ├── skills/detect-permafail/
    │   └── SKILL.md              (implementation from commands/)
    ├── commands/detect-permafail.md  (thin wrapper)
    └── .claude-plugin/plugin.json    (version bump to 0.0.40)

pr-ci-dashboard/
├── utils/ai_analyzer.py          (invokes /ci:detect-permafail command)
├── run.sh                        (checks for plugin installation)
└── .claude-plugin/plugin.json    (declares dependency on ci@ai-helpers)
```

### Migration Steps

1. **Create skill in ai-helpers** (branch: CORENET-7149)
   - Copy `commands/detect-permafail.md` to `plugins/ci/skills/detect-permafail/SKILL.md`
   - Update frontmatter to skill format
   - Bump plugin version to 0.0.40

2. **Create command wrapper**
   - New file: `plugins/ci/commands/detect-permafail.md`
   - Minimal wrapper that invokes the skill
   - Follows pattern from `prow-job-analyze-test-failure`

3. **Update pr-ci-dashboard**
   - Modify `utils/ai_analyzer.py` to invoke command instead of reading files
   - Update `run.sh` with plugin installation checks
   - Add dependency declaration in `.claude-plugin/plugin.json`

4. **Remove local files**
   - Delete `commands/detect-permafail.md`
   - Delete `.claude/skills` symlink

### API Contract

No changes to existing API:

```python
analyze_permafail(job_urls, job_name, pr_info) -> dict
analyze_permafail_streaming(job_urls, job_name, pr_info) -> generator
```

Return format remains unchanged:
```json
{
  "permafail": bool,
  "reason": str,
  "common_tests": [str],
  "signatures": [
    {
      "classification": str,
      "artifacts": {str: str},
      "flake_cluster": str
    }
  ]
}
```

### Plugin Dependency Management

The pr-ci-dashboard declares a plugin dependency:

```json
{
  "name": "pr-ci-dashboard",
  "version": "0.2.0",
  "dependencies": {
    "ci@ai-helpers": ">=0.0.40"
  }
}
```

When installed via plugin marketplace, this ensures the ai-helpers plugin is available. For local development (current workflow), `run.sh` validates installation.

## Testing Strategy

### CORENET-7148 Testing

**Manual Testing:**
1. Load dashboard with branch sync PR
2. Enable auto-retest toggle
3. Trigger job failure (retest via Prow comment)
4. Verify auto-retest triggers on 1st and 2nd failure
5. Verify permafail check on 3rd failure
6. Reload page, verify toggle state persists
7. Close tab, reopen, verify still enabled

**Edge Cases:**
- Multiple jobs failing simultaneously
- Permafail check timeout/failure
- Network interruption during polling
- Browser storage quota exceeded

### CORENET-7149 Testing

**Validation:**
1. Install ai-helpers with new skill on test branch
2. Update pr-ci-dashboard to consume from plugin
3. Trigger permafail analysis via `/api/jobs/analyze`
4. Verify identical results to current implementation
5. Check streaming endpoint (`/api/jobs/analyze-stream`)
6. Verify error handling (missing plugin, skill timeout)

**Regression Testing:**
- All existing permafail detection tests pass
- Analysis caching still works
- Override functionality unchanged

## Deployment

### CORENET-7148 Deployment
1. Merge frontend changes to main branch
2. Users refresh browser to get new JavaScript
3. No server restart required

### CORENET-7149 Deployment
1. Merge ai-helpers PR (new skill + command)
2. Users update ai-helpers plugin: `claude plugin update ci@ai-helpers`
3. Merge pr-ci-dashboard PR (consumes new plugin)
4. Run `./run.sh` - will check for plugin and error if missing

### Rollback Plan
- CORENET-7148: Revert static/app.js commit, users refresh browser
- CORENET-7149: Revert pr-ci-dashboard change, users continue using local skill

## Future Enhancements

### Auto-Retest Enhancements
- Configurable retry thresholds per job type
- Email/Slack notifications on permafail detection
- Analytics dashboard for auto-retest success rates
- Smart backoff based on Prow queue depth

### Multi-User Deployment
Separate design document to be created (see `docs/multi-user-deployment.md` for implementation guide).

Key points:
- XDG Base Directory pattern for user data
- Database moves to `~/.local/share/pr-ci-dashboard/`
- Remove local symlinks, rely on plugin system
- Each user uses their own gh and Claude credentials

## Open Questions

None. Design is ready for implementation.

## Success Criteria

### CORENET-7148 Success
- Auto-retest toggle appears on all PR cards
- Toggle state persists across browser sessions
- Jobs auto-retest on 1st and 2nd failure without permafail check
- Permafail check runs before 3rd retest attempt
- Auto-retest disables when permafail detected

### CORENET-7149 Success
- ai-helpers plugin contains detect-permafail skill
- pr-ci-dashboard successfully invokes skill via plugin
- All existing permafail functionality works unchanged
- No local skill files or symlinks remain
- Plugin installation validated in run.sh
