"""Flask server for PR CI Dashboard."""
import sys
import os
import argparse
from flask import Flask, jsonify, request, render_template
from .utils.script_fetcher import fetch_scripts
from .utils.gh_auth import check_gh_auth
from .utils.db import init_db, get_auto_retest_state, set_auto_retest_state, DB_PATH
from .api.search import search_prs
from .api.jobs import get_pr_jobs
from .api.retest import retest_jobs
from .api.analysis import analysis_bp

app = Flask(__name__)

# Configure database path
app.config['DB_PATH'] = DB_PATH

# Register blueprints
app.register_blueprint(analysis_bp)

# Global state
DEFAULT_QUERY = "is:pr is:open archived:false author:openshift-pr-manager[bot]"
CONFIG = {
    'port': 5000,
    'search_query': DEFAULT_QUERY,
    'db_path': DB_PATH,
    'debug': False
}


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
    """Get the default search query from config."""
    return jsonify({"query": CONFIG['search_query']})


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


@app.route('/api/auto-retest', methods=['GET'])
def api_auto_retest_get():
    """Get auto-retest enablement for all PRs: {"owner/repo/number": bool, ...}"""
    try:
        state = get_auto_retest_state(db_path=app.config.get('DB_PATH'))
        return jsonify(state)
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/api/auto-retest', methods=['POST'])
def api_auto_retest_set():
    """Set auto-retest enablement for a PR.

    Request: {"pr_key": "owner/repo/number", "enabled": bool}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    pr_key = data.get('pr_key')
    enabled = data.get('enabled')

    if not isinstance(pr_key, str) or not isinstance(enabled, bool):
        return jsonify({"error": "pr_key (string) and enabled (bool) required"}), 400

    # pr_key must be "owner/repo/number" with a numeric PR number
    parts = pr_key.split('/')
    if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2].isdigit():
        return jsonify({"error": "pr_key must be owner/repo/number"}), 400

    try:
        set_auto_retest_state(pr_key, enabled, db_path=app.config.get('DB_PATH'))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


def parse_cli_args(args=None):
    """
    Parse CLI arguments for dashboard configuration.

    Args:
        args: List of arguments to parse (defaults to sys.argv[1:])

    Returns:
        argparse.Namespace with parsed arguments
    """
    parser = argparse.ArgumentParser(
        prog='pr-ci-dashboard',
        description='OpenShift PR CI Dashboard - View and retest Prow jobs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Environment Variables:
  PR_CI_DASHBOARD_DB    Path to SQLite database (default: ~/.local/share/pr-ci-dashboard/dashboard.db)
  DASHBOARD_PORT        Port to run on (default: 5000)
  DASHBOARD_DEBUG       Set to 1/true to enable Flask debug mode (default: off)

Examples:
  pr-ci-dashboard --port 8080
  pr-ci-dashboard --search "label:approved"
  pr-ci-dashboard --search-override "is:pr is:open label:lgtm"
  pr-ci-dashboard --db-path /tmp/test.db
  pr-ci-dashboard author:jluhrsen repo:openshift/ovn-kubernetes
        '''
    )

    parser.add_argument(
        '--port',
        type=int,
        default=None,
        help='Port to run Flask server on (default: 5000, or DASHBOARD_PORT env var)'
    )

    # Make --search and --search-override mutually exclusive
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        '--search',
        type=str,
        default=None,
        help='Additional search terms to append to default query'
    )
    group.add_argument(
        '--search-override',
        type=str,
        default=None,
        help='Completely replace the default search query'
    )

    parser.add_argument(
        '--db-path',
        type=str,
        default=None,
        help='Path to SQLite database (overrides PR_CI_DASHBOARD_DB env var)'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        default=None,
        help='Enable Flask debug mode (development only - exposes the Werkzeug debugger)'
    )

    parser.add_argument(
        'search_terms',
        nargs='*',
        default=[],
        metavar='SEARCH_TERM',
        help='Additional search terms appended to the query (legacy positional form)'
    )

    return parser.parse_args(args)


def build_config(args, environ=None):
    """
    Build fresh configuration dict from CLI args and environment.

    Args:
        args: Parsed argparse.Namespace from parse_cli_args()
        environ: Environment dict (defaults to os.environ)

    Returns:
        dict: Fresh config with port, search_query, db_path
    """
    if environ is None:
        environ = os.environ

    config = {}

    # Port: CLI arg > DASHBOARD_PORT env var > default 5000
    if args.port is not None:
        config['port'] = args.port
    elif 'DASHBOARD_PORT' in environ:
        try:
            config['port'] = int(environ['DASHBOARD_PORT'])
        except ValueError:
            raise SystemExit(
                f"Invalid DASHBOARD_PORT value {environ['DASHBOARD_PORT']!r}: must be an integer")
    else:
        config['port'] = 5000

    # Database path: CLI arg > PR_CI_DASHBOARD_DB env var > default DB_PATH
    if args.db_path:
        config['db_path'] = os.path.abspath(args.db_path)
    elif 'PR_CI_DASHBOARD_DB' in environ:
        config['db_path'] = os.path.abspath(os.path.expanduser(environ['PR_CI_DASHBOARD_DB']))
    else:
        config['db_path'] = DB_PATH

    # Search query: --search-override completely replaces, --search appends
    if args.search_override:
        config['search_query'] = args.search_override
    elif args.search:
        config['search_query'] = DEFAULT_QUERY + " " + args.search
    else:
        config['search_query'] = DEFAULT_QUERY

    # Legacy positional search terms append to whatever query was built above
    if args.search_terms:
        config['search_query'] += " " + " ".join(args.search_terms)

    # Debug: CLI flag > DASHBOARD_DEBUG env var > default False
    # Never enable in production - the Werkzeug debugger allows code execution
    if args.debug is not None:
        config['debug'] = args.debug
    elif 'DASHBOARD_DEBUG' in environ:
        config['debug'] = environ['DASHBOARD_DEBUG'].strip().lower() in ('1', 'true', 'yes', 'on')
    else:
        config['debug'] = False

    return config


def main():
    """Start the Flask server."""
    # Parse CLI arguments (exits early for --help)
    args = parse_cli_args()

    print("🚀 PR CI Dashboard Starting...")

    # Build fresh configuration from args and environment
    global CONFIG
    CONFIG = build_config(args)

    # Always sync app.config['DB_PATH'] with CONFIG
    app.config['DB_PATH'] = CONFIG['db_path']

    # Initialize database
    try:
        init_db(CONFIG['db_path'])
        print(f"✅ Database initialized: {CONFIG['db_path']}")
    except Exception as e:
        print(f"❌ Failed to initialize database: {e}")
        print("Cannot start dashboard without database.")
        sys.exit(1)

    # Verify packaged scripts are accessible
    try:
        fetch_scripts()
    except Exception as e:
        print(f"❌ Failed to find packaged scripts: {e}")
        print("Cannot start dashboard without scripts.")
        sys.exit(1)

    # Check gh auth
    auth = check_gh_auth()
    if not auth["authenticated"]:
        print(f"⚠️  {auth['error']}")
        print("Dashboard will start but retest buttons will be disabled.")
    else:
        print("✅ GitHub CLI authenticated")

    print(f"\n🌐 Dashboard running at http://localhost:{CONFIG['port']}")
    print(f"📝 Search query: {CONFIG['search_query']}")
    if CONFIG['debug']:
        print("⚠️  Debug mode enabled - do NOT use in production")

    app.run(host='0.0.0.0', port=CONFIG['port'], debug=CONFIG['debug'])


if __name__ == '__main__':
    main()
