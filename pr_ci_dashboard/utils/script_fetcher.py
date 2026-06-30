"""Provide paths to local bash scripts."""
import os
from pathlib import Path

# Find scripts directory relative to this package
# After pip install, scripts are in pr_ci_dashboard/scripts/
SCRIPT_DIR = str(Path(__file__).parent.parent / 'scripts')


def fetch_scripts():
    """Verify packaged scripts exist and return script directory.

    Scripts are installed as package resources via pip. After installation,
    they are owned by the install user (often root in containers). The
    dashboard may run as non-root and should not attempt to modify package
    files at runtime.

    Scripts are executed via 'bash script_path', so executable bits are not
    required. Verification ensures required scripts are present as regular
    files.

    Returns:
        str: Path to the scripts directory

    Raises:
        Exception: If any required script is missing
    """
    required = ['e2e-retest.sh', 'common.sh', 'payload-retest.sh']
    for filename in required:
        path = os.path.join(SCRIPT_DIR, filename)
        if not os.path.isfile(path):
            raise Exception(f"Missing required script: {path}")
        print(f"  {filename} ready at {path}")
    return SCRIPT_DIR


def get_script_path(script_name):
    """Get full path to a script."""
    return os.path.join(SCRIPT_DIR, script_name)
