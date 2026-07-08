# Session Handoff — Flake Buster (PR CI Dashboard)

Short continuation note for resuming development (2026-07-08). Fuller
operational context lives with the maintainer outside this repository.

## Architecture in one paragraph

Containerized Flask dashboard (gunicorn) for viewing/retesting failed
OpenShift PR CI jobs with Claude-driven permafail analysis. Two independent
credential systems: (1) GitHub operations run as a GitHub App bot whose
private key is mounted at runtime (never in image/repo); connected users'
own tokens take priority, and bot-posted retest comments attribute the
requesting human. (2) Analysis runs as the signed-in human via Google OAuth
(PKCE; workspace-domain enforced; refresh tokens memory-only; per-run
transient credential files on RAM-backed tmp). All public identifiers the
image needs are baked into the Containerfile; the two real secrets (Google
client secret, App private key) are runtime inputs only. See README for
run recipes and `docs/specs/2026-07-02-security-hardening-plan.md` for the
security posture and remaining work.

## Local run (known-good invocation shape)

```bash
# once per machine: bot key from your team's secret manager -> podman secret
podman secret create fb-github-app-key ~/.config/fb-bot-key.pem
# once: runtime config (the two values the image does not bake)
printf 'GOOGLE_OAUTH_CLIENT_SECRET=<secret-from-your-team>\nANTHROPIC_VERTEX_PROJECT_ID=<your-vertex-project>\n' > ~/.config/fb.env && chmod 600 ~/.config/fb.env
# run - browse http://127.0.0.1:<host-port> (NOT localhost: rootless podman
# publishes IPv4 only); the OAuth redirect URI registered for the Google
# client must exactly match host+port in the browser address bar
podman run -d --name flake-buster -p 127.0.0.1:5000:5000 --env-file ~/.config/fb.env -v fb-data:/data --secret source=fb-github-app-key,type=mount,target=/secrets/github-app/private-key.pem,uid=1001,gid=0,mode=0400 quay.io/jluhrsen/pr-ci-dashboard:latest
```

Container listens on 5000 internally; any host port works via
`-p 127.0.0.1:<host-port>:5000`. Update = `podman pull` (once CI/quay is
current) + `podman rm -f flake-buster` + re-run; the fb-data volume keeps
the database.

## State at handoff

- GitHub bot chain field-verified end to end, including a live retest
  comment on a real PR.
- Google sign-in verified through to the dashboard; the full signed-in
  analysis run was still being tested — confirm its outcome first.
- Test suite green (216); frontend checked with `node --check`.

## Conventions

- Every change gets an independent review pass before it lands; review
  fixes are amended into the logical commit they belong to.
- Rebuild the container image after each commit; verify claims against a
  live container before reporting success.
- Single-line copy-pasteable commands in user-facing instructions.

## Next work (priority order)

1. Confirm the signed-in analysis run end to end.
2. Push git + image registry; redeploy the cluster with the new image and
   the App-key secret; retire the old shared-token secret.
3. CI registry credentials so image builds happen in CI.
4. Hardening plan remainders (TLS/Route, CVE scanning, POST-body job
   status, NetworkPolicy).
