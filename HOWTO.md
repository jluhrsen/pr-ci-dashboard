# Flake Buster - How To Guide

👻🚫 **Flake Buster** helps you quickly see and retest e2e/payload job failures in your PRs.

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/jluhrsen/workBits/main/pr-ci-dashboard/run.sh | sh
```

Open **http://localhost:5000**

Press Ctrl+C to stop and clean up.

### With Custom Search

```bash
curl -fsSL https://raw.githubusercontent.com/jluhrsen/workBits/main/pr-ci-dashboard/run.sh | sh -s -- author:jluhrsen repo:openshift/ovn-kubernetes
```

## Prerequisites

- **Python 3.8+**
- **GitHub CLI** authenticated:
  ```bash
  gh auth login
  gh auth status
  ```

## Manual Installation

```bash
git clone https://github.com/jluhrsen/workBits.git
cd workBits/pr-ci-dashboard
pip install -r requirements.txt
python server.py [search-args...]
```

### Search Examples

```bash
python server.py author:jluhrsen
python server.py repo:openshift/ovn-kubernetes
python server.py author:jluhrsen label:bug is:draft
```

Default: `is:pr is:open archived:false author:openshift-pr-manager[bot]`

## Using the Dashboard

- **Search bar**: Enter GitHub search syntax, press Enter
- **PR cards**: E2E jobs (left), Payload jobs (right)
- **Expand sections**: Click job headers to show/hide failed jobs
- **Retest**: Click button to trigger `/test` or `/payload-job` comment
  - Button shows "⏳ Retesting..." and polls until job starts running
- **PR links**: Click red PR number to open on GitHub

That's it! 🎉
