# Flake Buster - PR CI Dashboard

👻🚫 Dashboard for viewing and retesting failed OpenShift PR CI jobs.

![Flake Buster Dashboard](screenshot.png)

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/jluhrsen/pr-ci-dashboard/main/run.sh | sh
```

Then open **http://localhost:5000**

**🔒 Security Note:** The [run.sh script](run.sh) downloads this repo to `/tmp` and runs Python locally - no sudo, no permanent changes. Press Ctrl+C to stop and clean up. Review the script before running if concerned.

### With Custom Search

```bash
curl -fsSL https://raw.githubusercontent.com/jluhrsen/pr-ci-dashboard/main/run.sh | sh -s -- author:jluhrsen repo:openshift/ovn-kubernetes
```

## Features

- Search PRs using GitHub query syntax
- View failed e2e/payload jobs with consecutive failure counts
- One-click retest via local `gh` CLI
- Auto-polling after retest to detect when jobs start running

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

## Prerequisites

- **Python 3.8+**
- **GitHub CLI** (`gh`) authenticated - https://cli.github.com
  ```bash
  gh auth login
  gh auth status
  ```

## Using the Dashboard

- **Search bar**: Enter GitHub search syntax, press Enter
- **PR cards**: E2E jobs (left), Payload jobs (right)
- **Expand sections**: Click job headers to show/hide failed jobs
- **Retest**: Click button to trigger `/test` or `/payload-job` comment
  - Button shows "⏳ Retesting..." and polls until job starts running
- **PR links**: Click red PR number to open on GitHub

## Manual Installation

```bash
git clone https://github.com/jluhrsen/pr-ci-dashboard.git
cd pr-ci-dashboard
pip install -r requirements.txt
python server.py [search-args...]
```

**Custom search examples:**
```bash
python server.py author:jluhrsen
python server.py repo:openshift/ovn-kubernetes
python server.py author:jluhrsen label:bug is:draft
```

**Default search:** `is:pr is:open archived:false author:openshift-pr-manager[bot]`

## Architecture

- **Backend**: Flask server running bash scripts via subprocess
- **Frontend**: Vanilla JS with Red Hat theme
- **Scripts**: Local bash scripts in `scripts/` directory for PR search and job retesting
- **Auth**: Uses local `gh` CLI credentials (no OAuth setup needed)

## Project Structure

```
pr-ci-dashboard/
├── server.py           # Flask entry point
├── api/                # API endpoints (search, jobs, retest)
├── parsers/            # Parse script output
├── scripts/            # Bash scripts for PR search and job retesting
├── utils/              # Script executor, auth check
├── static/             # app.js, styles.css
└── templates/          # index.html
```

## Troubleshooting

**GitHub CLI not authenticated**
```bash
gh auth login
gh auth status
```

**Scripts timeout**
Increase timeout in `utils/job_executor.py` (default 30s)

## Documentation

- [docs/design.md](docs/design.md) - Complete design document

## License

Apache 2.0 - See [LICENSE](LICENSE)
