import subprocess
import json


def analyze_permafail(job_urls, job_name, pr_info):
    """
    Analyze job URLs for permafail pattern using Claude Code CLI

    Args:
        job_urls: List of 3 consecutive Prow job URLs
        job_name: Name of the job (e.g., "e2e-aws-ovn")
        pr_info: PR identifier (e.g., "openshift/ovn-kubernetes#1234")

    Returns:
        dict: Analysis result with permafail verdict and signatures

    Raises:
        RuntimeError: If skill execution fails or times out
    """
    urls_json = json.dumps(job_urls)

    cmd = [
        'claude-code',
        'skill',
        'pr-ci-dashboard:detect-permafail',
        f'--job-urls={urls_json}',
        f'--job-name={job_name}',
        f'--pr={pr_info}'
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode != 0:
            return {
                "permafail": False,
                "error": f"Skill execution failed: {result.stderr}",
                "signatures": []
            }

        return json.loads(result.stdout)

    except subprocess.TimeoutExpired:
        return {
            "permafail": False,
            "error": "Analysis timed out after 5 minutes",
            "signatures": []
        }
    except json.JSONDecodeError as e:
        return {
            "permafail": False,
            "error": f"Failed to parse skill output: {e}",
            "signatures": []
        }
    except Exception as e:
        return {
            "permafail": False,
            "error": f"Unexpected error: {e}",
            "signatures": []
        }
