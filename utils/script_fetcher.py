"""Provide paths to local bash scripts."""
import os

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')


def fetch_scripts():
    """Verify local scripts exist."""
    required = ['e2e-retest.sh', 'common.sh', 'payload-retest.sh']
    for filename in required:
        path = os.path.join(SCRIPT_DIR, filename)
        if not os.path.isfile(path):
            raise Exception(f"Missing required script: {path}")
        os.chmod(path, 0o755)
        print(f"  {filename} ready at {path}")
    return SCRIPT_DIR


def get_script_path(script_name):
    """Get full path to a script."""
    return os.path.join(SCRIPT_DIR, script_name)
