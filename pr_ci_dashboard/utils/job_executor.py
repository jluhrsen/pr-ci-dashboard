"""Execute bash scripts and parse output."""
import os
import subprocess
import json
from .script_fetcher import get_script_path


def _gh_env(token):
    """Subprocess env with an optional per-user GitHub token override."""
    if token:
        return {**os.environ, "GH_TOKEN": token}
    return None


def get_e2e_jobs(repo: str, pr_number: int, token: str = None) -> dict:
    """
    Execute e2e-retest.sh with --json flag and parse output.

    Args:
        token: Optional per-user GitHub token for the script's gh calls

    Returns:
        {"failed": [...], "running": [...]} or {"error": "message"}
    """
    script_path = get_script_path('e2e-retest.sh')

    try:
        # Use --json flag for structured output with URLs
        result = subprocess.run(
            ["bash", script_path, "--json", repo, str(pr_number)],
            capture_output=True,
            text=True,
            timeout=30,
            env=_gh_env(token)
        )

        if result.returncode != 0:
            return {
                "error": "Script failed",
                "stderr": result.stderr,
                "failed": [],
                "running": []
            }

        # Parse JSON output
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            return {
                "error": f"Failed to parse JSON: {e}",
                "failed": [],
                "running": []
            }

    except subprocess.TimeoutExpired:
        return {
            "error": "Script timed out",
            "failed": [],
            "running": []
        }
    except Exception as e:
        return {
            "error": str(e),
            "failed": [],
            "running": []
        }


def get_payload_jobs(repo: str, pr_number: int, token: str = None) -> dict:
    """
    Execute payload-retest.sh with --json flag and parse output.

    Args:
        token: Optional per-user GitHub token for the script's gh calls

    Returns:
        {"failed": [...], "running": [...]} or {"error": "message"}
    """
    script_path = get_script_path('payload-retest.sh')

    try:
        # Use --json flag for structured output with URLs
        result = subprocess.run(
            ["bash", script_path, "--json", repo, str(pr_number)],
            capture_output=True,
            text=True,
            timeout=30,
            env=_gh_env(token)
        )

        if result.returncode != 0:
            return {
                "error": "Script failed",
                "stderr": result.stderr,
                "failed": [],
                "running": []
            }

        # Parse JSON output
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            return {
                "error": f"Failed to parse JSON: {e}",
                "failed": [],
                "running": []
            }

    except subprocess.TimeoutExpired:
        return {
            "error": "Script timed out",
            "failed": [],
            "running": []
        }
    except Exception as e:
        return {
            "error": str(e),
            "failed": [],
            "running": []
        }
