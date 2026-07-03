# Security Hardening Plan — Shared Cluster Deployment (Phase 2)

**Status:** Planned, not started. Phase 1 (single-user cluster, port-forward access,
personal credentials as namespace secrets) is deployed and explicitly does NOT meet
these requirements. This document is the checklist for making the dashboard safe to
share with a team on a Red Hat IT-managed cluster.

## Threat model shift

Phase 1: only the cluster owner has access; port-forward is the auth layer; secrets
are personal, revocable tokens. Acceptable.

Phase 2 (shared cluster + Route + team users): cluster admins are not the app owner;
network reachability replaces port-forward; credentials must not act as any
individual unless that individual authenticated. Everything below follows from that.

## Target auth architecture (agreed direction)

Per-user OAuth end-to-end, no shared long-lived secrets:

1. **Login:** DONE 2026-07-02. "Sign in with Google" (Red Hat runs Google
   Workspace, so this IS Red Hat SSO): authorization-code web flow with PKCE
   and state validation in `utils/google_oauth.py` (stdlib, no authlib dep),
   using a self-service OAuth client in a personal GCP project (External
   audience + test-user allowlist; the client's project does NOT need to be
   the Vertex project). Redirect URI http://localhost:5000/... serves every
   port-forward user. Mandatory mode DONE 2026-07-03: DASHBOARD_REQUIRE_LOGIN=1
   gates every API endpoint behind a signed-in Google session (401 +
   frontend sign-in gate); login/csrf/healthz paths exempt.
2. **Vertex-as-user:** DONE 2026-07-02. The signed-in user's refresh token is
   packaged as an authorized_user ADC dict (same shape gcloud writes) and
   materialized to a transient file on RAM-backed /tmp (emptyDir
   medium: Memory in the deployment) only for the duration of each `claude`
   subprocess, then deleted. Signed-out sessions fall back to the pod's
   mounted credentials. Working Vertex combo (verified):
   `<internal-vertex-project>` + `CLOUD_ML_REGION=global` +
   `ANTHROPIC_MODEL=claude-opus-4-6` (model pin required — newer CLI default
   models 403 on some Vertex projects).
3. **GitHub-as-user:** DONE 2026-07-02 (device flow). With
   `GITHUB_OAUTH_CLIENT_ID` set, users connect via GitHub device flow (no
   client secret, no callback URL needed) and retest comments post as them;
   unconnected sessions fall back to the shared pod token. Tokens are held in
   an in-memory dict keyed by a signed session cookie. See
   `utils/github_oauth.py` and the /api/github/oauth/* endpoints.
   Remaining: reads (search/job status) still use the pod token; drop the
   fallback token entirely once login is mandatory.
4. **Sessions:** memory-only. No refresh tokens persisted to SQLite/disk. Pod
   restart = users re-login. Documented residual risk: an admin with pod exec can
   access tokens of ACTIVE sessions only.
   NOTE for the gunicorn migration: the in-memory session dicts are
   per-process, so multi-worker gunicorn needs a single worker, sticky
   sessions, or a shared store — decide when swapping the server.

**Interim option (ships faster):** openshift `oauth-proxy` sidecar for
authentication + shared service-account key for Vertex + per-user GitHub OAuth.
Uses a Red Hat-vetted component for login; defers the Google OAuth client work.

## External asks (blockers, need project/org owners)

- [ ] Register the dashboard as an **internal OAuth client** in Red Hat's GCP org
      (consent screen for redhat.com users).
- [ ] Vertex IAM for users: grant a team Google group `roles/aiplatform.user` on
      `<internal-ci-vertex-project>` (per-user design), OR obtain a dedicated
      service-account key (interim design). CI's key: secret
      `<ci-secret-name>` in `<ci-secrets-namespace>` on <ci-cluster> (<ci-team> Vault;
      owners <owner>/<owner>/<owner>/<owner>).
- [ ] Optional: ask the Vertex project owners to enable newer Claude models
      (data-sharing opt-in) so the model pin can advance.

## Must-fix before any shared/Route-exposed deployment

- [x] **Production WSGI server.** DONE 2026-07-03: non-debug mode runs
      gunicorn (gthread, workers=1 because OAuth session state is
      process-memory, threads=16, timeout 600s for SSE analyze streams);
      `--debug` keeps the Werkzeug dev server for development. The
      multi-worker/shared-store question is resolved by pinning workers=1.
- [ ] **TLS.** Route with edge or reencrypt termination; no plaintext HTTP.
- [x] **Session security.** DONE 2026-07-03 (partial): HttpOnly + SameSite=Lax
      set; Secure opt-in via DASHBOARD_SECURE_COOKIES=1 (Phase 1 port-forward
      is plain http). Server-side idle expiry already existed (session TTL);
      OAuth disconnects serve as logout. Remaining: flip Secure default on
      once TLS lands.
- [x] **CSRF protection.** DONE 2026-07-03: session-bound token from
      /api/csrf-token, enforced by a before_request hook on ALL non-GET /api/
      routes (blueprints included); frontend fetch interceptor attaches
      X-CSRF-Token everywhere. tests/test_csrf.py covers missing/wrong/valid
      token, cross-session token reuse, GETs unaffected, blueprint coverage.
- [ ] **OAuth flows via `authlib` with PKCE + state validation.** Never hand-roll.
- [x] **Input validation audit.** DONE 2026-07-03: utils/validation.py
      (reject-not-sanitize) enforced at every endpoint whose inputs reach a
      subprocess or the Claude prompt — retest (owner/repo/pr/job names),
      pr-jobs path params, analyze + analyze-stream (prow.ci.openshift.org
      URL allowlist, repo, job name, PR format), search (length + pagination
      bounds, and gh CLI flag injection blocked via `--` separator with our
      flags first). Script-side quoting audited: all args passed as
      subprocess lists, bash scripts quote and re-validate PR numbers.
      tests/test_validation.py covers module + endpoint enforcement.
      Original checklist follows:
      - `job_urls`: must match `https://prow.ci.openshift.org/view/...` (allowlist
        prefix), reject anything else before it reaches a prompt or script.
      - `owner`/`repo`/`pr`: strict `[A-Za-z0-9_.-]+` / integer.
      - search strings passed to `gh` via scripts: audit quoting in
        `pr_ci_dashboard/scripts/*.sh`.
      Injection surfaces: subprocess args, bash script interpolation, and prompt
      injection into the Claude CLI analysis prompt.
- [x] **Authorization on every API endpoint.** DONE 2026-07-03 via
      DASHBOARD_REQUIRE_LOGIN=1 (see login gate above); default remains off
      for Phase 1 port-forward compatibility. tests/test_login_gate.py.

## Hardening (expected in review, not launch-blocking)

- [x] Audit log. DONE 2026-07-03: audit_log SQLite table; retest, analyze,
      analyze-stream, override, delete-cache record actor (google email >
      github login > anonymous), target, result; GET /api/audit reads back.
      Audit failures never break the audited operation.
- [x] Rate limiting. DONE 2026-07-03: per-session sliding window
      (utils/rate_limit.py, thread-safe): retest 10/min, analyze 4/min
      (Claude subprocess cost); 429 on breach. tests/test_audit_rate_limit.py.
- [ ] Token handling: RAM-backed emptyDir (`medium: Memory`) for transient
      credential files; scrub after subprocess exit; never log tokens.
- [ ] Dependency/CVE scanning in CI for the image; rebuild cadence for base image.
- [ ] NetworkPolicy restricting ingress to the Route/oauth-proxy.
- [ ] Resource limits tuned; liveness probe moved to a lightweight `/healthz`
      (avoids rendering index.html; also add readiness variant).
- [x] Surface backend job-fetch errors in the UI. DONE 2026-07-03: job
      sections render a red "fetch failed" header and the error/stderr
      details (expanded) when the backend returns an `error` field, instead
      of a healthy-looking "0 failed / 0 running". API pass-through pinned by
      tests/test_jobs_error_passthrough.py.
- [ ] Streaming analyzer overall timeout (pre-existing bug: `while process.poll()`
      loop in `ai_analyzer.analyze_permafail_streaming` has no deadline; the
      TimeoutExpired handler is unreachable).
- [ ] SQLite → PostgreSQL when multi-replica or shared state is needed (Phase 3).
- [x] Move auto-retest enablement server-side. DONE 2026-07-02: state lives in
      the `auto_retest` SQLite table via GET/POST `/api/auto-retest`; the
      frontend migrates any legacy localStorage state to the server once on
      page load. (Previously browser localStorage keyed by origin — didn't
      follow users, leaked between backends on the same host:port.)
- [ ] GitOps delivery (ArgoCD) instead of laptop `oc apply`.

## Known credential-hygiene debt (Phase 1)

- Personal ADC / Claude tokens were used during Phase 1 bring-up and at least two
  were exposed in a chat transcript on 2026-07-01/02 and flagged for rotation.
  Phase 2 removes personal credentials from the cluster entirely.
