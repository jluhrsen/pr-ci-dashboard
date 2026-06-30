"""Check GitHub CLI authentication status."""
import subprocess


def check_gh_auth() -> dict:
    """
    Check if gh CLI is installed and authenticated.

    Returns:
        {
            "authenticated": bool,
            "error": str or None
        }
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=5
        )

        # gh auth status returns 0 if authenticated
        if result.returncode == 0:
            return {"authenticated": True, "error": None}
        else:
            return {
                "authenticated": False,
                "error": "Not authenticated. Run: gh auth login"
            }

    except FileNotFoundError:
        return {
            "authenticated": False,
            "error": "GitHub CLI not found. Install from: https://cli.github.com"
        }
    except Exception as e:
        return {
            "authenticated": False,
            "error": f"Error checking auth: {str(e)}"
        }


def post_retest_comment(owner: str, repo: str, pr: int, comment_body: str) -> dict:
    """
    Post a comment to a PR using gh CLI.

    Returns:
        {"success": True} or {"error": "message"}
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "comment", str(pr),
             "--repo", f"{owner}/{repo}",
             "--body", comment_body],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            # Check if auth error
            if "authentication" in result.stderr.lower():
                return {"error": "auth_failed"}
            return {"error": result.stderr}

        return {"success": True}

    except Exception as e:
        return {"error": str(e)}
