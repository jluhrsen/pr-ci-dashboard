# Flake Buster - PR CI Dashboard

Dashboard for viewing and retesting failed OpenShift PR CI jobs.

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/jluhrsen/workBits/main/pr-ci-dashboard/run.sh | sh
```

Then open **http://localhost:5000**

## Features

- Search PRs using GitHub query syntax
- View failed e2e/payload jobs with consecutive failure counts
- One-click retest via local `gh` CLI
- Auto-polling after retest to detect when jobs start running

## Prerequisites

- **Python 3.8+**
- **GitHub CLI** (`gh`) authenticated - https://cli.github.com
  ```bash
  gh auth login
  ```

## Manual Installation

```bash
git clone https://github.com/jluhrsen/workBits.git
cd workBits/pr-ci-dashboard
pip install -r requirements.txt
python server.py
```

### Custom Search

Pass GitHub search syntax as arguments:

```bash
python server.py author:jluhrsen
python server.py repo:openshift/ovn-kubernetes
python server.py author:jluhrsen label:bug is:draft
```

Default: `is:pr is:open archived:false author:openshift-pr-manager[bot]`

## Architecture

- **Backend**: Flask server running bash scripts via subprocess
- **Frontend**: Vanilla JS with Red Hat theme
- **Scripts**: Fetched from https://github.com/openshift-eng/ai-helpers/pull/177
- **Auth**: Uses local `gh` CLI credentials

## Project Structure

```
pr-ci-dashboard/
├── server.py           # Flask entry point
├── api/                # API endpoints (search, jobs, retest)
├── parsers/            # Parse script output
├── utils/              # Script fetcher, executor, auth check
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

**Failed to fetch scripts**
Check internet connection and verify PR #177 exists

## Configuration

**Environment Variables:**
- `AI_HELPERS_BRANCH`: GitHub branch to fetch scripts from (default: `refs/pull/177/head`)

## Documentation

- [HOWTO.md](HOWTO.md) - User guide
- [docs/design.md](docs/design.md) - Complete design document
