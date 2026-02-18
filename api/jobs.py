"""Fetch job status for a PR."""
from concurrent.futures import ThreadPoolExecutor
from utils.job_executor import get_e2e_jobs, get_payload_jobs


def get_pr_jobs(owner: str, repo: str, pr_number: int) -> dict:
    """
    Fetch e2e and payload job status for a PR.

    Runs both scripts in parallel for speed.

    Returns:
        {
            "pr": {"owner": "...", "repo": "...", "number": 123},
            "e2e": {"failed": [...], "running": [...]},
            "payload": {"failed": [...], "running": [...]}
        }
    """
    repo_full = f"{owner}/{repo}"

    # Run both in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        e2e_future = executor.submit(get_e2e_jobs, repo_full, pr_number)
        payload_future = executor.submit(get_payload_jobs, repo_full, pr_number)

        e2e_result = e2e_future.result()
        payload_result = payload_future.result()

    return {
        "pr": {
            "owner": owner,
            "repo": repo,
            "number": pr_number
        },
        "e2e": e2e_result,
        "payload": payload_result
    }
