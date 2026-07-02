"""GitHub OAuth device flow for per-user retest attribution.

Uses the device flow (RFC 8628) so no client secret is needed in the pod and
no OAuth callback URL has to resolve to the deployment (the dashboard is
typically accessed via kubectl port-forward). The only configuration is the
OAuth App client ID, which is public by design.

Tokens are held in server memory only (see docs/specs/2026-07-02-security-
hardening-plan.md): nothing is persisted, and a pod restart requires users to
reconnect.
"""
import json
import os
import urllib.request
import urllib.parse

DEVICE_CODE_URL = 'https://github.com/login/device/code'
ACCESS_TOKEN_URL = 'https://github.com/login/oauth/access_token'
USER_API_URL = 'https://api.github.com/user'

# Scope: commenting on public repos (all openshift/* PR retests). Use "repo"
# instead if private repositories ever need retesting.
OAUTH_SCOPE = 'public_repo'


def get_client_id():
    """Return the configured OAuth App client ID, or None if the feature is disabled."""
    return os.environ.get('GITHUB_OAUTH_CLIENT_ID') or None


def _post_form(url, fields, timeout=10):
    """POST form fields, return parsed JSON response."""
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        url, data=data, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def start_device_flow(client_id):
    """
    Begin the device flow.

    Returns:
        dict with device_code (server-side only), user_code, verification_uri,
        interval, expires_in

    Raises:
        RuntimeError: If GitHub rejects the request
    """
    result = _post_form(DEVICE_CODE_URL, {
        'client_id': client_id,
        'scope': OAUTH_SCOPE,
    })
    if 'device_code' not in result:
        raise RuntimeError(f"Device flow start failed: {result.get('error_description') or result}")
    return result


def poll_device_flow(client_id, device_code):
    """
    Poll GitHub once for the device flow result.

    Returns:
        {"status": "success", "token": str} when authorized
        {"status": "pending"} while the user has not yet entered the code
        {"status": "slow_down", "interval": int} if polling too fast
        {"status": "error", "error": str} on terminal failure (denied/expired)
    """
    result = _post_form(ACCESS_TOKEN_URL, {
        'client_id': client_id,
        'device_code': device_code,
        'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
    })

    if 'access_token' in result:
        return {"status": "success", "token": result['access_token']}

    error = result.get('error', 'unknown_error')
    if error == 'authorization_pending':
        return {"status": "pending"}
    if error == 'slow_down':
        return {"status": "slow_down", "interval": result.get('interval', 10)}
    return {"status": "error", "error": result.get('error_description') or error}


def get_github_login(token):
    """
    Fetch the GitHub login (username) for a token.

    Returns:
        str login, or None if the token is invalid
    """
    request = urllib.request.Request(USER_API_URL, headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode()).get('login')
    except Exception:
        return None
