"""Google OAuth web flow (authorization code + PKCE) for per-user Vertex AI.

Users sign in with their redhat.com Google account; the resulting refresh
token is packaged as an authorized_user ADC dict so `claude` subprocesses
can run Vertex analysis as that user. This mirrors what `gcloud auth
application-default login` produces, but through the dashboard's own OAuth
client (GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET env vars) so the
flow works for remote browsers.

The registered redirect URI must be http://<dashboard-host>/api/google/oauth/callback
(with port-forward access, http://localhost:5000/... works for every user).
"""
import base64
import hashlib
import json
import os
import secrets as py_secrets
import urllib.request
import urllib.parse

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'

# openid+email for identity display; cloud-platform is what Vertex needs
OAUTH_SCOPE = 'openid email https://www.googleapis.com/auth/cloud-platform'


def get_client_config():
    """Return (client_id, client_secret) or None if the feature is disabled."""
    client_id = os.environ.get('GOOGLE_OAUTH_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET')
    if client_id and client_secret:
        return client_id, client_secret
    return None


def make_pkce_pair():
    """Return (code_verifier, code_challenge) for PKCE (S256)."""
    verifier = py_secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip('=')
    return verifier, challenge


def build_auth_url(client_id, redirect_uri, state, code_challenge):
    """Build the Google authorization URL the browser is redirected to."""
    params = urllib.parse.urlencode({
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': OAUTH_SCOPE,
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        # offline + consent guarantees a refresh token, which the
        # authorized_user ADC format requires
        'access_type': 'offline',
        'prompt': 'consent',
    })
    return f'{AUTH_URL}?{params}'


def _post_form(url, fields, timeout=10):
    """POST form fields, return parsed JSON response."""
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        url, data=data, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def exchange_code(client_id, client_secret, code, redirect_uri, code_verifier):
    """
    Exchange the authorization code for tokens.

    Returns:
        dict with refresh_token, access_token, id_token, ...

    Raises:
        RuntimeError: If Google rejects the exchange or omits a refresh token
    """
    result = _post_form(TOKEN_URL, {
        'client_id': client_id,
        'client_secret': client_secret,
        'code': code,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
        'code_verifier': code_verifier,
    })
    if 'refresh_token' not in result:
        raise RuntimeError(
            f"Token exchange failed: {result.get('error_description') or result.get('error') or 'no refresh token returned'}")
    return result


def email_from_id_token(id_token, client_id=None):
    """
    Extract the email claim from an id_token JWT payload.

    Signature verification is intentionally skipped, per Google's OIDC
    documentation for the server-side flow: when the ID token is received
    directly from Google's token endpoint over an intermediary-free HTTPS
    channel (as here, in exchange_code), "you can be confident that the
    token you receive really comes from Google and is valid" without local
    validation. As defense in depth we still check the parseable claims:
    iss must be Google, aud must be our client_id (when given), and exp
    must be in the future. The email is used for session attribution and
    display; the refresh token is the actual credential.
    """
    import time
    try:
        payload_b64 = id_token.split('.')[1]
        payload_b64 += '=' * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))

        if payload.get('iss') not in ('https://accounts.google.com', 'accounts.google.com'):
            return None
        if client_id is not None and payload.get('aud') != client_id:
            return None
        exp = payload.get('exp')
        if not isinstance(exp, (int, float)) or exp < time.time():
            return None

        return payload.get('email')
    except Exception:
        return None


def build_adc(client_id, client_secret, refresh_token):
    """Build an authorized_user ADC dict, the same shape gcloud writes.

    google-auth libraries (including the one inside Claude Code) accept this
    via GOOGLE_APPLICATION_CREDENTIALS and mint access tokens on demand.
    """
    return {
        'type': 'authorized_user',
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
    }
