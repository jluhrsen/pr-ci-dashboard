"""Execute bash scripts and parse output."""
import subprocess
from parsers.e2e_parser import parse_e2e_output
from parsers.payload_parser import parse_payload_output
from utils.script_fetcher import get_script_path


def get_e2e_jobs(repo: str, pr_number: int) -> dict:
    """
    Execute e2e-retest.sh and parse output.

    Returns:
        {"failed": [...], "running": [...]} or {"error": "message"}
    """
    script_path = get_script_path('e2e-retest.sh')

    try:
        # Pipe "4" to select "Just show list (done)"
        result = subprocess.run(
            ["bash", script_path, repo, str(pr_number)],
            input="4\n",
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return {
                "error": "Script failed",
                "stderr": result.stderr,
                "failed": [],
                "running": []
            }

        return parse_e2e_output(result.stdout)

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


def get_payload_jobs(repo: str, pr_number: int) -> dict:
    """
    Execute payload-retest.sh and parse output.

    Returns:
        {"failed": [...], "running": [...]} or {"error": "message"}
    """
    script_path = get_script_path('payload-retest.sh')

    try:
        # Pipe "3" to select "Just show list (done)"
        result = subprocess.run(
            ["bash", script_path, repo, str(pr_number)],
            input="3\n",
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return {
                "error": "Script failed",
                "stderr": result.stderr,
                "failed": [],
                "running": []
            }

        return parse_payload_output(result.stdout)

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
