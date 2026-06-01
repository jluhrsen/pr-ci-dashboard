# Multi-User Deployment Solution for PR CI Dashboard

## Problem Statement

The pr-ci-dashboard currently has user-specific dependencies that prevent easy multi-user deployment:
1. `.claude/skills` symlink points to user-specific home directory
2. Database stored in project directory (lost on cleanup)
3. No checks for Claude CLI availability until runtime failure
4. Multiple users running `curl | sh` would each need their own credentials

## Solution: XDG Base Directory Pattern

Follow the same pattern as `gh` CLI and other modern Unix tools - system-wide installation with per-user configuration and data.

## File Structure

```
~/.config/pr-ci-dashboard/
  └── config.json                  # User preferences (non-sensitive)

~/.local/share/pr-ci-dashboard/
  └── dashboard.db                 # User's analysis cache (persists across runs)

/tmp/pr-ci-dashboard-$USER/        # Run location (cleaned on exit)
  └── <cloned repo>
```

## Implementation Tasks

### Task 1: Update Database Location

**File: `server.py`**

```python
from pathlib import Path
import os

# Add after imports:
DATA_DIR = Path.home() / '.local' / 'share' / 'pr-ci-dashboard'
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'dashboard.db'

# Update Flask config:
app = Flask(__name__)
app.config['DB_PATH'] = str(DB_PATH)
```

**File: `utils/db.py`**

```python
# Remove hardcoded DB_PATH
# OLD:
# DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'dashboard.db')

# NEW: Get from Flask config
def get_db_path():
    from flask import current_app
    return current_app.config.get('DB_PATH')

# Update all function signatures to accept db_path parameter
def store_analysis(..., db_path=None):
    if db_path is None:
        db_path = get_db_path()
    # ... rest of function
```

### Task 2: Fix Skills Dependency

**Remove `.claude/skills` symlink entirely.**

The ai-helpers plugin provides the ci-prow-navigation skill, so no local symlink is needed. Update code to rely on the plugin system.

**File: `utils/ai_analyzer.py`**

Update to invoke the command directly rather than reading skill files:

```python
def analyze_permafail(job_urls, job_name, pr_info):
    """
    Analyze job URLs for permafail using ci:detect-permafail command from ai-helpers plugin
    
    Prerequisites:
    - Claude CLI must be installed
    - ci@ai-helpers plugin must be installed
    """
    import subprocess
    import json
    
    cmd = [
        'claude',
        '--allowedTools', 'Skill,WebFetch,Bash',
        '--print'
    ]
    
    prompt = f"""Use the /ci:detect-permafail command to analyze these jobs:

Job URLs: {json.dumps(job_urls)}
Job name: {job_name}
PR: {pr_info}

Return ONLY the final JSON result with no additional explanation."""
    
    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        if result.returncode != 0:
            return {
                "permafail": False,
                "error": f"Command execution failed: {result.stderr}",
                "signatures": []
            }
        
        output = result.stdout.strip()
        # Parse JSON from output (existing logic)
        ...
        
    except Exception as e:
        return {
            "permafail": False,
            "error": f"Unexpected error: {e}",
            "signatures": []
        }
```

### Task 3: Enhanced run.sh Checks

**File: `run.sh`**

Add comprehensive checks before running:

```bash
#!/bin/bash
set -e

echo "🔍 Checking prerequisites..."

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 not found. Please install Python 3.8+"
    exit 1
fi

# Check gh CLI
if ! command -v gh &> /dev/null; then
    echo "❌ Error: GitHub CLI (gh) not found."
    echo "Install from: https://cli.github.com"
    exit 1
fi

# Check gh auth
if ! gh auth status &> /dev/null; then
    echo "❌ Error: GitHub CLI not authenticated."
    echo "Run: gh auth login"
    exit 1
fi
echo "✅ GitHub CLI authenticated"

# Check Claude CLI
if ! command -v claude &> /dev/null; then
    echo "❌ Error: Claude CLI not found."
    echo "Install from: https://claude.ai/claude-code"
    exit 1
fi
echo "✅ Claude CLI found"

# Check ai-helpers plugin
if ! claude plugin list 2>/dev/null | grep -q 'ci@ai-helpers'; then
    echo "⚠️  Warning: ci@ai-helpers plugin not installed."
    echo "Permafail detection will not work without it."
    echo ""
    echo "To install:"
    echo "  claude plugin marketplace add openshift-eng/ai-helpers"
    echo "  claude plugin install ci@ai-helpers"
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "✅ ai-helpers plugin installed"
fi

# Rest of existing run.sh logic...
```

### Task 4: User Configuration Support

**File: `~/.config/pr-ci-dashboard/config.json`** (optional, for future enhancements)

```json
{
  "poll_interval_ms": 30000,
  "auto_retest_threshold": 2,
  "permafail_check_at": 3,
  "theme": "default"
}
```

**File: `server.py`**

```python
def load_user_config():
    """Load user configuration from XDG config directory"""
    config_dir = Path.home() / '.config' / 'pr-ci-dashboard'
    config_file = config_dir / 'config.json'
    
    default_config = {
        'poll_interval_ms': 30000,
        'auto_retest_threshold': 2,
        'permafail_check_at': 3
    }
    
    if config_file.exists():
        with open(config_file) as f:
            user_config = json.load(f)
            default_config.update(user_config)
    
    return default_config

# Add endpoint to serve config to frontend:
@app.route('/api/config')
def get_config():
    return jsonify(load_user_config())
```

## Multi-User Scenarios

### Scenario 1: Multiple Team Members, Separate Machines
Each user runs `curl | sh` on their own machine:
- ✅ Each has their own `~/.config/gh/` credentials
- ✅ Each has their own `~/.claude/` credentials  
- ✅ Each has their own `~/.local/share/pr-ci-dashboard/dashboard.db`
- ✅ No conflicts, no shared state

### Scenario 2: Multiple Team Members, Same Machine
Each user runs on the same shared server:
- ✅ Each has their own home directory with isolated config
- ✅ Flask runs on different ports or in different terminals
- ⚠️  Potential port conflicts (5000) - add port auto-selection
- ⚠️  Shared GitHub API rate limits via same organization

### Scenario 3: Centralized Deployment (Future)
For true multi-user server deployment with authentication:
- Need Flask OAuth implementation
- Session-based credential management
- Per-user database partitioning
- This is beyond current scope - document as future enhancement

## Testing Plan

1. **Test fresh install on clean VM:**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/jluhrsen/pr-ci-dashboard/main/run.sh | sh
   ```
   Verify all prerequisite checks work

2. **Test with missing prerequisites:**
   - Run without `gh auth login` - should error clearly
   - Run without Claude CLI - should error clearly
   - Run without ai-helpers plugin - should warn

3. **Test database persistence:**
   - Run dashboard, trigger permafail analysis
   - Stop server (Ctrl+C)
   - Run again - verify analysis cache persists in `~/.local/share/pr-ci-dashboard/dashboard.db`

4. **Test multi-user on same machine:**
   - User A runs dashboard on port 5000
   - User B runs dashboard - should either use different port or show clear error

## Future Enhancements

- Port auto-selection if 5000 is busy
- `~/.config/pr-ci-dashboard/config.json` for user preferences
- Environment variable overrides (`DASHBOARD_PORT`, `DASHBOARD_DB_PATH`)
- System-wide installation option (`/usr/local/bin/pr-ci-dashboard`)
- Docker container with volume mounts for user config

## Security Considerations

- Database file in `~/.local/share/` has mode 0600 (user-only read/write)
- No credentials stored by dashboard - relies on `gh` and `claude` CLI
- Each user's credentials remain in their own `~/.config/` directories
- No shared tokens, no shared secrets between users
