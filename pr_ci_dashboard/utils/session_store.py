"""In-memory per-session OAuth state (never persisted).

Tokens live only in these dicts, keyed by the random sid in the signed
session cookie. A pod restart drops everything (users reconnect), and idle
sessions are pruned after SESSION_TTL seconds of inactivity so a closed
browser doesn't leave its tokens in server memory indefinitely. Activity
slides the TTL window.

NOTE for the gunicorn migration: these dicts are per-process; multi-worker
deployments need a single worker, sticky sessions, or a shared store.
"""
import os
import time
import secrets as py_secrets

from flask import session

# sid -> {"token": str, "login": str, "last_seen": float} once connected
GITHUB_SESSIONS = {}
# sid -> {"device_code": str, "interval": int, "expires_at": float} while
# a GitHub device flow is pending
PENDING_DEVICE_FLOWS = {}
# sid -> {"adc": dict, "email": str, "last_seen": float} once signed in
# with Google (adc is an authorized_user credentials dict for Vertex)
GOOGLE_SESSIONS = {}

SESSION_TTL = int(os.environ.get(
    'DASHBOARD_SESSION_TTL_SECONDS',
    os.environ.get('GITHUB_SESSION_TTL_SECONDS', 8 * 3600)))


def session_id():
    """Get or create the random ID tying this browser session to server-side state."""
    if 'sid' not in session:
        session['sid'] = py_secrets.token_urlsafe(32)
    return session['sid']


def prune():
    """Drop expired pending device flows and idle connected sessions."""
    now = time.time()
    for sid in [s for s, flow in PENDING_DEVICE_FLOWS.items()
                if now > flow.get('expires_at', 0)]:
        PENDING_DEVICE_FLOWS.pop(sid, None)
    for sessions in (GITHUB_SESSIONS, GOOGLE_SESSIONS):
        for sid in [s for s, entry in sessions.items()
                    if now > entry.get('last_seen', 0) + SESSION_TTL]:
            sessions.pop(sid, None)


def _get_current(sessions):
    """Prune, then return (and touch) the current session's entry, or None."""
    prune()
    sid = session.get('sid')
    entry = sessions.get(sid) if sid else None
    if entry:
        entry['last_seen'] = time.time()
    return entry


def get_session_github():
    """Return {"token", "login"} for the current session, or None."""
    return _get_current(GITHUB_SESSIONS)


def get_session_google():
    """Return {"adc", "email"} for the current session, or None."""
    return _get_current(GOOGLE_SESSIONS)


def current_actor():
    """Best identity for audit purposes: Google email, else GitHub login,
    else "anonymous"."""
    google = get_session_google()
    if google:
        return google['email']
    github = get_session_github()
    if github:
        return github['login']
    return 'anonymous'
