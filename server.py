"""Flask server for PR CI Dashboard."""
import sys
from flask import Flask, jsonify, request, render_template
from utils.script_fetcher import fetch_scripts
from utils.gh_auth import check_gh_auth
from api.search import search_prs
from api.jobs import get_pr_jobs
from api.retest import retest_jobs

app = Flask(__name__)

# Global state
DEFAULT_QUERY = "is:pr is:open archived:false author:openshift-pr-manager[bot]"
CLI_ARGS = []


@app.route('/')
def index():
    """Serve main dashboard page."""
    return render_template('index.html')


@app.route('/api/auth/status')
def auth_status():
    """Check GitHub CLI authentication status."""
    return jsonify(check_gh_auth())


@app.route('/api/default-query')
def default_query():
    """Get the default search query (base + CLI args)."""
    query = DEFAULT_QUERY
    if CLI_ARGS:
        query += " " + " ".join(CLI_ARGS)
    return jsonify({"query": query})


@app.route('/api/search', methods=['POST'])
def api_search():
    """Search for PRs."""
    data = request.get_json()
    query = data.get('query', '')
    page = data.get('page', 1)
    per_page = data.get('per_page', 10)

    result = search_prs(query, page, per_page)
    return jsonify(result)


@app.route('/api/pr/<owner>/<repo>/<int:pr_number>')
def api_pr_jobs(owner, repo, pr_number):
    """Get job status for a PR."""
    result = get_pr_jobs(owner, repo, pr_number)
    return jsonify(result)


@app.route('/api/retest', methods=['POST'])
def api_retest():
    """Post retest comment to PR."""
    data = request.get_json()

    owner = data.get('owner')
    repo = data.get('repo')
    pr = data.get('pr')
    jobs = data.get('jobs', [])
    job_type = data.get('type', 'e2e')

    if not all([owner, repo, pr, jobs]):
        return jsonify({"error": "Missing required fields"}), 400

    result = retest_jobs(owner, repo, pr, jobs, job_type)
    return jsonify(result)


def parse_cli_args():
    """Parse CLI arguments as search query additions."""
    global CLI_ARGS
    # Skip script name, collect remaining args
    CLI_ARGS = sys.argv[1:]
    print(f"CLI args: {CLI_ARGS}")


def main():
    """Start the Flask server."""
    print("🚀 PR CI Dashboard Starting...")

    # Parse CLI arguments
    parse_cli_args()

    # Fetch scripts from GitHub
    try:
        fetch_scripts()
    except Exception as e:
        print(f"❌ Failed to fetch scripts: {e}")
        print("Cannot start dashboard without scripts.")
        sys.exit(1)

    # Check gh auth
    auth = check_gh_auth()
    if not auth["authenticated"]:
        print(f"⚠️  {auth['error']}")
        print("Dashboard will start but retest buttons will be disabled.")
    else:
        print("✅ GitHub CLI authenticated")

    print("\n🌐 Dashboard running at http://localhost:5000")
    print(f"📝 Default search: {DEFAULT_QUERY}")
    if CLI_ARGS:
        print(f"   + CLI args: {' '.join(CLI_ARGS)}")

    app.run(host='0.0.0.0', port=5000, debug=True)


if __name__ == '__main__':
    main()
