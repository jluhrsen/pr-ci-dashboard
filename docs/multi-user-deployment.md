# Multi-user deployment and authentication

This guide describes the multi-user features implemented in `main`. The
checked-in Kubernetes manifests are not yet a turnkey install; see
[issue #5](https://github.com/jluhrsen/pr-ci-dashboard/issues/5).

## Supported topology

The current shared topology is one pod and one gunicorn worker:

```text
browser sessions
  ├─ signed Flask cookie -> in-memory GitHub token
  ├─ signed Flask cookie -> in-memory Google ADC
  └─ CSRF token
          |
          v
one Flask/gunicorn process
  ├─ gh / GitHub App calls
  ├─ Claude subprocesses
  └─ SQLite on a ReadWriteOnce volume
```

Do not scale beyond one process/replica. OAuth sessions and rate limits are
in memory, and SQLite plus the browser-owned auto-retest design are not a
multi-replica coordination mechanism.

## Secret model

Public identifiers may be set in the image or deployment:

- GitHub OAuth client ID;
- Google OAuth client ID;
- GitHub App ID;
- OAuth hosted-domain restriction;
- expected secret mount paths.

Secrets must be provided only at runtime:

- `GOOGLE_OAUTH_CLIENT_SECRET`;
- GitHub App PEM private key;
- optional service-account ADC or `GH_TOKEN`;
- `DASHBOARD_SECRET_KEY` for stable signed cookies.

The checked-in Containerfile bakes the maintainer's public client/App IDs and
the `redhat.com` restriction. Build a differently configured image or override
the environment for another organization.

## GitHub identities

### Per-user device flow

Set `GITHUB_OAUTH_CLIENT_ID` to an OAuth App with Device Flow enabled. No client
secret or callback URL is required.

With `DASHBOARD_REQUIRE_GITHUB=1`, search, job discovery, and retest endpoints
return 401 until the user connects. The access token is stored only in process
memory and is removed on disconnect, idle expiry, or restart.

The requested scope is `public_repo`. Private-repository support would require
a different scope and an explicit security review.

### GitHub App bot fallback

Set:

```text
GITHUB_APP_ID=<public-app-id>
GITHUB_APP_PRIVATE_KEY_FILE=/secrets/github-app/private-key.pem
GITHUB_APP_ORG=openshift
```

When the file exists, the server signs a short-lived JWT with `openssl`, finds
the organization installation, and caches an installation token until five
minutes before expiry.

A connected user's token still has priority. If organization OAuth policy
blocks that token, retest can fall back to the App and add human attribution
to the comment. When no user is connected, App-posted comments use the best
available audit actor (Google email or `anonymous`).

Keep the host PEM mode 0600 and mount it read-only. Never bake it into an image.

### Ambient fallback

Without a connected user or configured App, subprocesses inherit ambient
`GH_TOKEN`/`gh` authentication. This is suitable only for a trusted single-user
process. The current server binds to all interfaces, so protect it with a
firewall until
[issue #8](https://github.com/jluhrsen/pr-ci-dashboard/issues/8) is fixed.

## Google login for Vertex analysis

Set:

```text
GOOGLE_OAUTH_CLIENT_ID=<web-client-id>
GOOGLE_OAUTH_CLIENT_SECRET=<secret>
GOOGLE_OAUTH_HOSTED_DOMAIN=redhat.com
DASHBOARD_REQUIRE_LOGIN=1
```

Create a Google OAuth web client and register the exact URL users browse:

```text
http://127.0.0.1:5000/api/google/oauth/callback
```

Host, port, scheme, and `localhost` versus `127.0.0.1` must match exactly.
Register every supported access URL. Use HTTPS and
`DASHBOARD_SECURE_COOKIES=1` for any routed shared deployment.

The flow uses authorization code + PKCE and requests OpenID email plus
`cloud-platform`. The server enforces issuer, audience, expiration, and the
configured Workspace `hd` claim before creating a session.

The refresh token remains in process memory. For each analysis, the server
writes an authorized-user ADC file, passes its path only to the Claude
subprocess, and deletes it afterward. Mount `/tmp` as a memory-backed volume.

Without a connected Google session, Claude inherits pod-level credentials.
Set `DASHBOARD_REQUIRE_LOGIN=1` when that fallback should not be available to
anonymous users.

## Application session settings

- Set a long random `DASHBOARD_SECRET_KEY` in the deployment. A random key is
  generated otherwise, invalidating cookies on every restart.
- `DASHBOARD_SESSION_TTL_SECONDS` defaults to 28,800 seconds (eight hours).
- Set `DASHBOARD_SECURE_COOKIES=1` only when the browser uses HTTPS.
- Cookies are HttpOnly and SameSite=Lax.
- State-changing API calls require a session-bound `X-CSRF-Token`.

CSRF is defense against cross-site browser requests, not authorization. A
direct client that can reach an ungated server can obtain its own token.

## Example Podman shape

Prepare an environment file containing only runtime values:

```bash
printf 'GOOGLE_OAUTH_CLIENT_SECRET=<secret>\nANTHROPIC_VERTEX_PROJECT_ID=<project>\nDASHBOARD_SECRET_KEY=<random-secret>\n' > ~/.config/fb.env
chmod 600 ~/.config/fb.env
```

Optionally create the App key secret:

```bash
podman secret create fb-github-app-key ~/.config/fb-bot-key.pem
```

Run:

```bash
podman run -d --name flake-buster \
  -p 127.0.0.1:5000:5000 \
  --env-file ~/.config/fb.env \
  -v fb-data:/data \
  --secret source=fb-github-app-key,type=mount,target=/secrets/github-app/private-key.pem,uid=1001,gid=0,mode=0400 \
  quay.io/jluhrsen/pr-ci-dashboard:latest
```

Omit the `--secret` option if users must connect GitHub individually.

## Kubernetes/OpenShift requirements

A productionized overlay should provide:

- one replica and one worker;
- persistent `/data`;
- memory-backed `/tmp`;
- stable `DASHBOARD_SECRET_KEY`;
- Google client secret and, optionally, App key as Secrets;
- organization-specific Vertex project/model configuration;
- HTTPS Route/Ingress plus secure cookies;
- NetworkPolicy and an explicit ingress allowlist;
- an image pinned by immutable tag or digest;
- platform-appropriate PVC ownership (`fsGroup: 0` is not accepted by
  OpenShift restricted SCC).

The App key and Google client secret should be independently rotatable.
Restarting the pod after rotation clears all in-memory user sessions.

## Operational limitations

- Auto-retest state is shared, but each browser executes it independently.
  Multiple tabs/users can post duplicates, and monitoring stops when browsers
  close. See [issue #2](https://github.com/jluhrsen/pr-ci-dashboard/issues/2).
- One process is a scalability and availability limit.
- There is no role distinction: every authenticated user can retest, analyze,
  change monitor state, override cached verdicts, and read the audit endpoint.
- Rate limits are per in-memory session and can be bypassed by creating new
  sessions.
- OAuth tokens are intentionally lost at restart.
- The repository does not include TLS, Route/Ingress, NetworkPolicy, backup,
  or disaster-recovery resources.

## Path to multiple replicas

Before adding replicas:

1. Move OAuth sessions and distributed rate limits to a shared store.
2. Replace or redesign SQLite for concurrent writers and migrations.
3. Move auto-retest to a server-side coordinator with leases/idempotency.
4. Add role-based authorization for mutations and audit access.
5. Add shared caching/request coalescing for PR job discovery.
6. Define database backup, retention, and secret-rotation procedures.

Relevant tracking issues:

- [#2 — duplicate-safe server-side auto-retest](https://github.com/jluhrsen/pr-ci-dashboard/issues/2)
- [#5 — deployable Kubernetes/OpenShift manifests](https://github.com/jluhrsen/pr-ci-dashboard/issues/5)
- [#6 — bounded and cached polling](https://github.com/jluhrsen/pr-ci-dashboard/issues/6)
- [#7 — reproducible production builds](https://github.com/jluhrsen/pr-ci-dashboard/issues/7)
- [#8 — safe local bind defaults](https://github.com/jluhrsen/pr-ci-dashboard/issues/8)
