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

1. **Login:** "Sign in with Google" (Red Hat runs Google Workspace, so this IS
   Red Hat SSO). Flask OAuth web flow via `authlib`, `cloud-platform` scope.
2. **Vertex-as-user:** each user's OAuth token is used for their Claude/Vertex
   calls. Backend materializes a per-user `authorized_user` credentials file on a
   RAM-backed emptyDir only for the duration of each `claude` subprocess, then
   deletes it. Verified feasible: user credentials work against
   `<internal-ci-vertex-project>` + `CLOUD_ML_REGION=global` +
   `ANTHROPIC_MODEL=claude-opus-4-6` (model pin required — CLI default model 403s).
3. **GitHub-as-user:** GitHub OAuth flow; retest comments posted as the clicking
   user. This makes the dashboard's permission model match Prow's automatically
   (Prow only honors /test from authorized users).
4. **Sessions:** memory-only. No refresh tokens persisted to SQLite/disk. Pod
   restart = users re-login. Documented residual risk: an admin with pod exec can
   access tokens of ACTIVE sessions only.

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

- [ ] **Production WSGI server.** Replace Flask/Werkzeug dev server
      (`app.run()`) with gunicorn. Reviewers auto-flag the dev server; it is
      single-threaded-ish, unhardened, and its debug mode is one env var away.
- [ ] **TLS.** Route with edge or reencrypt termination; no plaintext HTTP.
- [ ] **Session security.** HttpOnly/Secure/SameSite cookies, server-side session
      expiry, logout.
- [ ] **CSRF protection** on all state-changing endpoints: `/api/retest`,
      `/api/jobs/analyze`, `/api/jobs/analyze-stream`, `/api/jobs/override`,
      `/api/jobs/delete-cache`.
- [ ] **OAuth flows via `authlib` with PKCE + state validation.** Never hand-roll.
- [ ] **Input validation audit.** Backend shells out to bash scripts and builds
      Claude prompts from request JSON. Validate:
      - `job_urls`: must match `https://prow.ci.openshift.org/view/...` (allowlist
        prefix), reject anything else before it reaches a prompt or script.
      - `owner`/`repo`/`pr`: strict `[A-Za-z0-9_.-]+` / integer.
      - search strings passed to `gh` via scripts: audit quoting in
        `pr_ci_dashboard/scripts/*.sh`.
      Injection surfaces: subprocess args, bash script interpolation, and prompt
      injection into the Claude CLI analysis prompt.
- [ ] **Authorization on every API endpoint** once login exists (session required;
      no anonymous retest/analyze/override).

## Hardening (expected in review, not launch-blocking)

- [ ] Audit log: who triggered retest/analyze/override/delete-cache, when, result.
- [ ] Rate limiting on retest and analyze endpoints (abuse/cost control).
- [ ] Token handling: RAM-backed emptyDir (`medium: Memory`) for transient
      credential files; scrub after subprocess exit; never log tokens.
- [ ] Dependency/CVE scanning in CI for the image; rebuild cadence for base image.
- [ ] NetworkPolicy restricting ingress to the Route/oauth-proxy.
- [ ] Resource limits tuned; liveness probe moved to a lightweight `/healthz`
      (avoids rendering index.html; also add readiness variant).
- [ ] Surface backend job-fetch errors in the UI. `utils/job_executor.py`
      returns `{"error": ..., "failed": [], "running": []}` on script failure,
      and the frontend renders the empty lists as a healthy "0 failed / 0
      running" without showing the error (bit us in deployment: missing jq in
      the image looked like all-green PRs). The UI should show a visible error
      state when the `error` field is present.
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
