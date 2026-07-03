"""Flask server for PR CI Dashboard."""
import sys
import os
import time
import secrets as py_secrets
import argparse
from flask import Flask, jsonify, request, render_template, session, redirect
from .utils.script_fetcher import fetch_scripts
from .utils.gh_auth import check_gh_auth
from .utils import github_oauth
from .utils import google_oauth
from .utils.session_store import (
    GITHUB_SESSIONS, PENDING_DEVICE_FLOWS, GOOGLE_SESSIONS,
    SESSION_TTL as GITHUB_SESSION_TTL,
    session_id as _session_id,
    prune as _prune_github_state,
    get_session_github, get_session_google, current_actor,
)
from .utils.db import (init_db, get_auto_retest_state, set_auto_retest_state,
                       record_audit, get_audit_log, DB_PATH)
from .utils import validation
from .utils import rate_limit

# Rate limit per session: (max events, window seconds). Retests post GitHub
# comments. The analyze limit lives in api/analysis.py with its endpoints.
RETEST_RATE = (10, 60)
from .api.search import search_prs
from .api.jobs import get_pr_jobs
from .api.retest import retest_jobs
from .api.analysis import analysis_bp

app = Flask(__name__)

# Session cookie signing key. A random key per process means sessions (and
# the in-memory OAuth tokens they point at) do not survive a restart, which
# is the intended memory-only token model.
app.secret_key = os.environ.get('DASHBOARD_SECRET_KEY') or py_secrets.token_hex(32)

# Session cookie hardening. Secure is opt-in because Phase 1 access is plain
# http via kubectl port-forward; set DASHBOARD_SECURE_COOKIES=1 behind TLS.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get(
        'DASHBOARD_SECURE_COOKIES', '').strip().lower() in ('1', 'true', 'yes', 'on'),
)

# Configure database path
app.config['DB_PATH'] = DB_PATH

# CSRF protection for state-changing API calls (session-bound token, sent by
# the frontend in X-CSRF-Token). Disabled only by tests that aren't
# exercising CSRF itself.
app.config.setdefault('CSRF_ENABLED', True)


@app.route('/api/csrf-token')
def api_csrf_token():
    """Issue (or re-issue) the session's CSRF token for the frontend."""
    token = session.get('csrf_token')
    if not token:
        token = py_secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return jsonify({"token": token})


def _login_required_enabled():
    """Mandatory-login gate: DASHBOARD_REQUIRE_LOGIN truthy AND Google OAuth
    configured (the gate is meaningless without a way to log in)."""
    flag = os.environ.get('DASHBOARD_REQUIRE_LOGIN', '').strip().lower() in ('1', 'true', 'yes', 'on')
    return flag and google_oauth.get_client_config() is not None


# Paths that must work before login: the login flow itself, the CSRF token
# (session-bound, needed for the first POST after login), and health probes
LOGIN_EXEMPT_PREFIXES = ('/api/google/oauth/', '/healthz')
LOGIN_EXEMPT_PATHS = ('/api/csrf-token',)


@app.before_request
def require_login():
    """With DASHBOARD_REQUIRE_LOGIN on, every API endpoint needs a signed-in
    Google session. Non-API paths (index, static) stay reachable so the
    sign-in UI can render; the frontend gates itself on 401s."""
    if not _login_required_enabled():
        return None
    path = request.path
    if not path.startswith('/api/'):
        return None
    if path in LOGIN_EXEMPT_PATHS or any(path.startswith(p) for p in LOGIN_EXEMPT_PREFIXES):
        return None
    if get_session_google() is None:
        return jsonify({"error": "login_required"}), 401
    return None


@app.before_request
def csrf_protect():
    """Reject state-changing API requests without a valid session CSRF token.

    SameSite=Lax already blocks cross-site POSTs in modern browsers; this is
    defense in depth. Applies to blueprint routes too.
    """
    if not app.config.get('CSRF_ENABLED', True):
        return None
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return None
    if not request.path.startswith('/api/'):
        return None

    token = session.get('csrf_token')
    header = request.headers.get('X-CSRF-Token')
    if not token or not header or not py_secrets.compare_digest(header, token):
        return jsonify({"error": "CSRF token missing or invalid"}), 403
    return None

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

    if not isinstance(query, str) or len(query) > 512:
        return jsonify({"error": "Invalid query"}), 400
    if not isinstance(page, int) or not isinstance(per_page, int) \
            or page < 1 or not (1 <= per_page <= 100):
        return jsonify({"error": "Invalid pagination"}), 400

    result = search_prs(query, page, per_page)
    return jsonify(result)


@app.route('/api/pr/<owner>/<repo>/<int:pr_number>')
def api_pr_jobs(owner, repo, pr_number):
    """Get job status for a PR."""
    # owner/repo become bash script arguments
    if not validation.valid_name(owner) or not validation.valid_name(repo) \
            or not validation.valid_pr_number(pr_number):
        return jsonify({"error": "Invalid owner/repo/PR"}), 400

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

    # These values become gh CLI arguments and PR comment content
    if not validation.valid_name(owner) or not validation.valid_name(repo):
        return jsonify({"error": "Invalid owner/repo"}), 400
    if not validation.valid_pr_number(pr):
        return jsonify({"error": "Invalid PR number"}), 400
    if not isinstance(jobs, list) or not all(validation.valid_job_name(j) for j in jobs):
        return jsonify({"error": "Invalid job name(s)"}), 400

    if not rate_limit.allow(f'retest:{_session_id()}', *RETEST_RATE):
        return jsonify({"error": "Rate limit exceeded; try again shortly"}), 429

    # Post as the connected GitHub user when available; otherwise fall back
    # to the pod-level GH_TOKEN (Phase 1 behavior)
    github = get_session_github()
    token = github['token'] if github else None

    result = retest_jobs(owner, repo, pr, jobs, job_type, token=token)
    record_audit(current_actor(), 'retest', f"{owner}/{repo}#{pr} {jobs}",
                 'success' if result.get('success') else f"error: {result.get('error')}",
                 db_path=app.config.get('DB_PATH'))
    return jsonify(result)


@app.route('/api/audit')
def api_audit():
    """Most recent audit entries, newest first. ?limit=N (default 100)."""
    try:
        limit = int(request.args.get('limit', 100))
    except ValueError:
        return jsonify({"error": "Invalid limit"}), 400
    try:
        return jsonify(get_audit_log(limit=limit, db_path=app.config.get('DB_PATH')))
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/api/github/oauth/status')
def api_github_oauth_status():
    """Report whether per-user GitHub OAuth is configured and connected."""
    client_id = github_oauth.get_client_id()
    github = get_session_github()
    return jsonify({
        "enabled": client_id is not None,
        "connected": github is not None,
        "login": github['login'] if github else None
    })


@app.route('/api/github/oauth/start', methods=['POST'])
def api_github_oauth_start():
    """Start the GitHub device flow for this session.

    Returns user_code and verification_uri for the user to enter on GitHub.
    The device_code stays server-side.
    """
    client_id = github_oauth.get_client_id()
    if not client_id:
        return jsonify({"error": "GitHub OAuth not configured (GITHUB_OAUTH_CLIENT_ID unset)"}), 400

    try:
        flow = github_oauth.start_device_flow(client_id)
    except Exception as e:
        return jsonify({"error": f"Failed to start GitHub device flow: {e}"}), 502

    sid = _session_id()
    PENDING_DEVICE_FLOWS[sid] = {
        "device_code": flow['device_code'],
        "interval": flow.get('interval', 5),
        "expires_at": time.time() + flow.get('expires_in', 900)
    }

    return jsonify({
        "user_code": flow['user_code'],
        "verification_uri": flow['verification_uri'],
        "interval": flow.get('interval', 5),
        "expires_in": flow.get('expires_in', 900)
    })


@app.route('/api/github/oauth/poll', methods=['POST'])
def api_github_oauth_poll():
    """Poll the pending device flow once. Called by the frontend on an interval."""
    _prune_github_state()
    client_id = github_oauth.get_client_id()
    sid = session.get('sid')
    pending = PENDING_DEVICE_FLOWS.get(sid) if sid else None
    if not client_id or not pending:
        return jsonify({"error": "No device flow in progress"}), 400

    try:
        result = github_oauth.poll_device_flow(client_id, pending['device_code'])
    except Exception as e:
        return jsonify({"error": f"Failed to poll GitHub: {e}"}), 502

    if result['status'] == 'success':
        PENDING_DEVICE_FLOWS.pop(sid, None)
        login = github_oauth.get_github_login(result['token'])
        if not login:
            return jsonify({"status": "error", "error": "Token obtained but user lookup failed"})
        GITHUB_SESSIONS[sid] = {
            "token": result['token'],
            "login": login,
            "last_seen": time.time()
        }
        return jsonify({"status": "success", "login": login})

    if result['status'] == 'error':
        PENDING_DEVICE_FLOWS.pop(sid, None)

    return jsonify(result)


@app.route('/api/github/oauth/disconnect', methods=['POST'])
def api_github_oauth_disconnect():
    """Drop the session's GitHub token from server memory."""
    sid = session.get('sid')
    if sid:
        GITHUB_SESSIONS.pop(sid, None)
        PENDING_DEVICE_FLOWS.pop(sid, None)
    return jsonify({"success": True})


@app.route('/api/google/oauth/status')
def api_google_oauth_status():
    """Report whether Google sign-in is configured and connected."""
    config = google_oauth.get_client_config()
    google = get_session_google()
    return jsonify({
        "enabled": config is not None,
        "connected": google is not None,
        "email": google['email'] if google else None,
        "login_required": _login_required_enabled()
    })


@app.route('/api/google/oauth/login')
def api_google_oauth_login():
    """Redirect the browser to Google's consent screen (web flow + PKCE)."""
    config = google_oauth.get_client_config()
    if not config:
        return jsonify({"error": "Google OAuth not configured (GOOGLE_OAUTH_CLIENT_ID/SECRET unset)"}), 400
    client_id, _ = config

    _session_id()
    state = py_secrets.token_urlsafe(16)
    verifier, challenge = google_oauth.make_pkce_pair()
    # Signed cookie: tamper-proof, and only this browser gets the callback
    session['google_oauth_state'] = state
    session['google_oauth_verifier'] = verifier

    redirect_uri = request.host_url.rstrip('/') + '/api/google/oauth/callback'
    return redirect(google_oauth.build_auth_url(client_id, redirect_uri, state, challenge))


@app.route('/api/google/oauth/callback')
def api_google_oauth_callback():
    """Handle Google's redirect back: verify state, exchange code, store session."""
    config = google_oauth.get_client_config()
    if not config:
        return jsonify({"error": "Google OAuth not configured"}), 400
    client_id, client_secret = config

    if request.args.get('error'):
        # Clear the stale state/verifier so they can't linger
        session.pop('google_oauth_state', None)
        session.pop('google_oauth_verifier', None)
        return redirect('/?google_auth=denied')

    state = request.args.get('state')
    code = request.args.get('code')
    expected_state = session.pop('google_oauth_state', None)
    verifier = session.pop('google_oauth_verifier', None)
    if not code or not state or not expected_state or state != expected_state or not verifier:
        return jsonify({"error": "Invalid OAuth state"}), 400

    redirect_uri = request.host_url.rstrip('/') + '/api/google/oauth/callback'
    try:
        tokens = google_oauth.exchange_code(client_id, client_secret, code, redirect_uri, verifier)
    except Exception as e:
        print(f"[ERROR] Google token exchange failed: {e}")
        return redirect('/?google_auth=failed')

    email = google_oauth.email_from_id_token(tokens.get('id_token', ''), client_id=client_id) or 'unknown'
    sid = _session_id()
    GOOGLE_SESSIONS[sid] = {
        "adc": google_oauth.build_adc(client_id, client_secret, tokens['refresh_token']),
        "email": email,
        "last_seen": time.time()
    }
    return redirect('/')


@app.route('/api/google/oauth/disconnect', methods=['POST'])
def api_google_oauth_disconnect():
    """Drop the session's Google credentials from server memory."""
    sid = session.get('sid')
    if sid:
        GOOGLE_SESSIONS.pop(sid, None)
    return jsonify({"success": True})


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


def run_gunicorn(flask_app, port):
    """Run the app under gunicorn (production server).

    Single worker: the OAuth session stores live in process memory, so
    multiple workers would split sessions between processes. Threads provide
    the concurrency (parallel job fetches, multiple SSE analyze streams).
    """
    from gunicorn.app.base import BaseApplication

    class DashboardApplication(BaseApplication):
        def load_config(self):
            self.cfg.set('bind', f'0.0.0.0:{port}')
            self.cfg.set('workers', 1)
            self.cfg.set('worker_class', 'gthread')
            self.cfg.set('threads', 16)
            # SSE analyze streams legitimately run up to 5 minutes
            self.cfg.set('timeout', 600)
            self.cfg.set('accesslog', '-')

        def load(self):
            return flask_app

    DashboardApplication().run()


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
        # Werkzeug dev server with reloader/debugger - development only
        print("⚠️  Debug mode enabled - do NOT use in production")
        app.run(host='0.0.0.0', port=CONFIG['port'], debug=True)
    else:
        run_gunicorn(app, CONFIG['port'])


if __name__ == '__main__':
    main()
