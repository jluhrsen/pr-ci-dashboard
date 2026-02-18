"""Post retest comments to PRs."""
from utils.gh_auth import post_retest_comment


def retest_jobs(owner: str, repo: str, pr: int, jobs: list, job_type: str) -> dict:
    """
    Post retest comment for jobs.

    Args:
        owner: GitHub org/user
        repo: Repository name
        pr: PR number
        jobs: List of job names
        job_type: "e2e" or "payload"

    Returns:
        {"success": True} or {"error": "message"}
    """
    if not jobs:
        return {"error": "No jobs specified"}

    # Build comment body
    if job_type == "e2e":
        lines = [f"/test {job}" for job in jobs]
    elif job_type == "payload":
        lines = [f"/payload-job {job}" for job in jobs]
    else:
        return {"error": f"Invalid job type: {job_type}"}

    comment_body = "\n".join(lines)

    return post_retest_comment(owner, repo, pr, comment_body)
