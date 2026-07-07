"""GitHub App installation tokens for the bot fallback identity.

The openshift-pr-manager GitHub App's private key (mounted as a file, never
in the image or repo) signs short-lived JWTs that mint ~1-hour installation
tokens. Those tokens perform gh operations as openshift-pr-manager[bot] —
the account that authors the PRs this dashboard manages, so the openshift
org's OAuth-app restrictions don't apply (GitHub Apps are installed, not
third-party-authorized).

Ports the proven flow from openshift/release
ci-operator/step-registry/github/branch-sync (JWT via openssl, installation
lookup, access-token mint). Tokens are cached and refreshed shortly before
expiry; any failure returns None so callers fall back to ambient gh auth.
"""
import base64
import json
import os
import subprocess
import threading
import time
import urllib.request

API_ROOT = 'https://api.github.com'

_lock = threading.Lock()
# org -> {"token": str, "expires_at": float}
_cache = {}


def _app_id():
    return os.environ.get('GITHUB_APP_ID') or None


def _key_file():
    return os.environ.get('GITHUB_APP_PRIVATE_KEY_FILE') or None


def _org():
    return os.environ.get('GITHUB_APP_ORG', 'openshift')


def configured():
    """True when an App ID is set and the private key file is present."""
    key_file = _key_file()
    return bool(_app_id()) and bool(key_file) and os.path.isfile(key_file)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def make_jwt(app_id, key_file, now=None):
    """RS256 app JWT, signed via openssl (no Python crypto dependency).

    Raises:
        RuntimeError: If openssl fails (bad/missing key)
    """
    if now is None:
        now = int(time.time())
    header = _b64url(b'{"alg":"RS256","typ":"JWT"}')
    # iat backdated 60s for clock skew, 10 minute lifetime (GitHub max)
    payload = _b64url(json.dumps(
        {"iat": now - 60, "exp": now + 540, "iss": int(app_id)}).encode())
    signing_input = f'{header}.{payload}'.encode()

    result = subprocess.run(
        ['openssl', 'dgst', '-sha256', '-sign', key_file],
        input=signing_input, capture_output=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"JWT signing failed: {result.stderr.decode(errors='replace').strip()}")

    return f'{header}.{payload}.{_b64url(result.stdout)}'


def _api(url, jwt, method='GET'):
    request = urllib.request.Request(url, method=method, headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {jwt}',
    })
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def get_bot_token():
    """Cached installation token for the configured org, or None.

    None means "not configured or minting failed" — callers fall back to
    ambient gh auth. Failures are printed, never raised.
    """
    if not configured():
        return None

    org = _org()
    now = time.time()
    with _lock:
        cached = _cache.get(org)
        # Refresh with 5 minutes of validity to spare (tokens live ~1h)
        if cached and cached['expires_at'] - now > 300:
            return cached['token']

        try:
            jwt = make_jwt(_app_id(), _key_file())
            installation = _api(f'{API_ROOT}/orgs/{org}/installation', jwt)
            grant = _api(
                f"{API_ROOT}/app/installations/{installation['id']}/access_tokens",
                jwt, method='POST')

            # expires_at is ISO8601 Zulu, e.g. 2026-07-07T01:23:45Z
            from datetime import datetime
            expires_at = datetime.fromisoformat(
                grant['expires_at'].replace('Z', '+00:00')).timestamp()

            _cache[org] = {"token": grant['token'], "expires_at": expires_at}
            return grant['token']
        except Exception as e:
            print(f"[ERROR] GitHub App token mint failed for org {org}: {e}")
            return None


def reset_cache():
    """Clear cached tokens (tests)."""
    with _lock:
        _cache.clear()
