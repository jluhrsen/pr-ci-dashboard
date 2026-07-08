"""Post retest comments to PRs."""
from ..utils.gh_auth import post_retest_comment


def retest_jobs(owner: str, repo: str, pr: int, jobs: list, job_type: str,
                token: str = None, requested_by: str = None, auto: bool = False) -> dict:
    """
    Post retest comment for jobs.

    Args:
        owner: GitHub org/user
        repo: Repository name
        pr: PR number
        jobs: List of job names
        job_type: "e2e" or "payload"
        token: Optional per-user GitHub token (comment posted as that user)
        requested_by: When the posting identity is not the human (bot or
            shared token), attribute the real requester in the comment.
            Prow ignores non-command lines, so this is safe to append.
        auto: True when the dashboard's auto-retester triggered this

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

    if requested_by:
        verb = "auto-retest triggered for" if auto else "retest requested by"
        lines += ["", f"👻🚫 {verb} `{requested_by}` via Flake Buster"]

    comment_body = "\n".join(lines)

    return post_retest_comment(owner, repo, pr, comment_body, token=token)
