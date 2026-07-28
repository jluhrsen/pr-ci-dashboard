"""Fetch job status for a PR."""
from concurrent.futures import ThreadPoolExecutor
from ..utils.job_executor import get_e2e_jobs, get_payload_jobs, get_pr_state


def get_pr_jobs(owner: str, repo: str, pr_number: int, token: str = None) -> dict:
    """
    Fetch e2e and payload job status for a PR.

    Runs all lookups in parallel for speed.

    Returns:
        {
            "pr": {"owner": "...", "repo": "...", "number": 123, "state": "OPEN"},
            "e2e": {"failed": [...], "running": [...]},
            "payload": {"failed": [...], "running": [...]}
        }
    """
    repo_full = f"{owner}/{repo}"

    with ThreadPoolExecutor(max_workers=3) as executor:
        e2e_future = executor.submit(get_e2e_jobs, repo_full, pr_number, token)
        payload_future = executor.submit(get_payload_jobs, repo_full, pr_number, token)
        state_future = executor.submit(get_pr_state, repo_full, pr_number, token)

        e2e_result = e2e_future.result()
        payload_result = payload_future.result()
        state_result = state_future.result()

    return {
        "pr": {
            "owner": owner,
            "repo": repo,
            "number": pr_number,
            "state": state_result.get("state", "UNKNOWN")
        },
        "e2e": e2e_result,
        "payload": payload_result
    }
