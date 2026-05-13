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
        dict: Analysis result with permafail verdict and signatures.
              On error, returns dict with permafail=False, error message, and empty signatures list.
    """
    import os

    # Get the project root directory (where .claude-plugin/ exists)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Build prompt to invoke the skill
    urls_json = json.dumps(job_urls)
    prompt = f"/pr-ci-dashboard:detect-permafail --job-urls={urls_json} --job-name={job_name} --pr={pr_info}"

    cmd = [
        'claude',
        '--print'
    ]

    try:
        result = subprocess.run(
            cmd,
            input=prompt,  # Pass prompt via stdin
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

        # Extract JSON from output (skill may output explanatory text before JSON)
        output = result.stdout.strip()

        if not output:
            return {
                "permafail": False,
                "error": "Skill returned empty output",
                "signatures": []
            }

        # Try parsing as pure JSON first
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            pass

        # If that fails, try to find JSON object in output
        # Look for the last occurrence of a complete JSON object
        json_start = output.rfind('{')
        if json_start == -1:
            return {
                "permafail": False,
                "error": f"No JSON found in skill output. Output: {output[:200]}",
                "signatures": []
            }

        json_str = output[json_start:]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            return {
                "permafail": False,
                "error": f"Failed to parse JSON from output: {e}. Output snippet: {output[:200]}",
                "signatures": []
            }

    except subprocess.TimeoutExpired:
        return {
            "permafail": False,
            "error": "Analysis timed out after 5 minutes",
            "signatures": []
        }
    except Exception as e:
        return {
            "permafail": False,
            "error": f"Unexpected error: {e}",
            "signatures": []
        }
