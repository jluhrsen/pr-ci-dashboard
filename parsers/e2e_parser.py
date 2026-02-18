"""Parse e2e-retest.sh output."""
import re

def parse_e2e_output(output: str) -> dict:
    """
    Parse e2e-retest.sh text output.

    Expected format:
        Failed e2e jobs:
          ❌ e2e-aws-ovn
             Consecutive failures: 5
             Recent history: 8 fail / 2 pass / 0 abort
        ⏳ Currently running (2 jobs):
          • e2e-metal-ipi

    Returns:
        {
            "failed": [{"name": "job-name", "consecutive": 5}, ...],
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
