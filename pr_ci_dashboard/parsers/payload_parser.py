"""Parse payload-retest.sh output."""
import re

def parse_payload_output(output: str) -> dict:
    """
    Parse payload-retest.sh text output.

    Expected format:
        Failed payload jobs:
          ❌ periodic-ci-openshift-ovn-kubernetes-release-4.18-e2e-aws-ovn
             Consecutive failures: 3
        ⏳ Currently running (1 jobs):
          • periodic-ci-...

    Returns:
        {
            "failed": [{"name": "job-name", "consecutive": 3}, ...],
            "running": ["job-name", ...]
        }
    """
    failed_jobs = []
    running_jobs = []

    # Extract failed jobs with consecutive count
    failed_pattern = r'❌ (.+?)\n\s+Consecutive failures: (\d+)'
    for match in re.finditer(failed_pattern, output, re.MULTILINE):
        job_name = match.group(1).strip()
        consecutive = int(match.group(2))
        failed_jobs.append({"name": job_name, "consecutive": consecutive})

    # Extract running jobs
    running_section = re.search(
        r'Currently running.*?:\n(.*?)(?:\n\n|$)',
        output,
        re.DOTALL
    )
    if running_section:
        running_pattern = r'• (.+?)(?:\n|$)'
        for match in re.finditer(running_pattern, running_section.group(1)):
            running_jobs.append(match.group(1).strip())

    return {"failed": failed_jobs, "running": running_jobs}
