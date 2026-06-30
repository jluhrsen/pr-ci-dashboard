"""PR search via GitHub CLI."""
import subprocess
import json
import shlex


def search_prs(query: str, page: int = 1, per_page: int = 10) -> dict:
    """
    Search PRs using GitHub CLI.

    Note: GitHub CLI doesn't support pagination, so we fetch up to 1000 results
    and implement pagination client-side.

    Returns:
        {
            "prs": [
                {
                    "number": 123,
                    "title": "...",
                    "owner": "openshift",
                    "repo": "ovn-kubernetes",
                    "author": "user",
                    "created_at": "2024-01-01T00:00:00Z",
                    "state": "OPEN"
                },
                ...
            ],
            "total": 47
        }
    """
    try:
        # Use gh search to find PRs
        # GitHub CLI doesn't support pagination, so fetch more results upfront
        # Note: GitHub API limits search results to 1000 max
        query_args = shlex.split(query) if query else []
        cmd = ["gh", "search", "prs"] + query_args + ["--limit", "1000", "--json", "number,title,repository,author,createdAt,state"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            return {"error": result.stderr, "prs": [], "total": 0}

        raw_prs = json.loads(result.stdout)

        # Transform to our format
        all_prs = []
        for pr in raw_prs:
            repo_full = pr.get("repository", {})
            # Parse owner/repo from nameWithOwner (e.g., "openshift/ovn-kubernetes")
            name_with_owner = repo_full.get("nameWithOwner", "")
            if "/" in name_with_owner:
                owner, repo = name_with_owner.split("/", 1)
            else:
                owner = ""
                repo = repo_full.get("name", "")

            all_prs.append({
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "owner": owner,
                "repo": repo,
                "author": pr.get("author", {}).get("login", ""),
                "created_at": pr.get("createdAt", ""),
                "state": pr.get("state", "UNKNOWN")
            })

        # Implement pagination on the results
        total = len(all_prs)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        prs = all_prs[start_idx:end_idx]

        return {"prs": prs, "total": total}

    except Exception as e:
        return {"error": str(e), "prs": [], "total": 0}
