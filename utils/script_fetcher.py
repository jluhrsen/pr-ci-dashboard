"""Fetch bash scripts from GitHub on startup."""
import os
import requests

# Script source can be configured via environment variable
# Default: jluhrsen fork with --json flag support for URL extraction
# Production: Will change to openshift-eng/ai-helpers main after PRs merge
# TODO: Update to openshift-eng/ai-helpers main branch after PRs merge
AI_HELPERS_REPO = os.environ.get('AI_HELPERS_REPO', 'jluhrsen/ai-helpers')
AI_HELPERS_BRANCH = os.environ.get('AI_HELPERS_BRANCH', 'add-job-urls-to-retest-scripts')
BASE_URL = f"https://raw.githubusercontent.com/{AI_HELPERS_REPO}/{AI_HELPERS_BRANCH}/plugins/ci/skills"

SCRIPT_DIR = "/tmp/pr-ci-dashboard-scripts"

def fetch_scripts():
    """Download scripts from GitHub to local temp directory."""
    os.makedirs(SCRIPT_DIR, exist_ok=True)

    # Scripts to download from GitHub
    scripts = {
        'e2e-retest.sh': f"{BASE_URL}/e2e-retest/e2e-retest.sh",
        'common.sh': f"{BASE_URL}/e2e-retest/common.sh",
        'payload-retest.sh': f"{BASE_URL}/payload-retest/payload-retest.sh",
    }

    for filename, url in scripts.items():
        local_path = os.path.join(SCRIPT_DIR, filename)

        try:
            print(f"Fetching {filename} from GitHub...")
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            with open(local_path, 'w') as f:
                f.write(response.text)

            os.chmod(local_path, 0o755)
            print(f"✅ {filename} ready at {local_path}")

        except requests.RequestException as e:
            raise Exception(f"Failed to fetch {filename} from GitHub: {e}")

    return SCRIPT_DIR

def get_script_path(script_name):
    """Get full path to a fetched script."""
    return os.path.join(SCRIPT_DIR, script_name)
