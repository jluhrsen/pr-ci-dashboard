# Current architecture

This document describes the implementation in `main`. Dated plans and specs
under `docs/plans`, `docs/specs`, and `docs/superpowers` are historical design
records; they are not the current operating guide.

## Purpose

Flake Buster reduces the manual work involved in monitoring OpenShift pull
requests. It combines GitHub PR search, Prow and payload-job discovery,
targeted retest comments, and optional AI classification of repeated failures.

The application is intentionally small:

- a Flask application served by gunicorn;
- one vanilla JavaScript page;
- Bash helpers for data sources whose existing interfaces are HTML/CLI based;
- SQLite for cached analysis, monitor settings, and audit entries.

## Runtime flow

### Search and card loading

1. The browser fetches `/api/default-query` and POSTs `/api/search`.
2. The backend runs `gh search prs --limit 1000`, transforms the results, and
   returns one Python-sliced page.
3. The browser renders up to 30 cards and requests `/api/pr/<owner>/<repo>/<n>`
   for each card.
4. Each PR request runs the e2e and payload helpers in parallel.
5. The JSON results include failed jobs, running jobs, consecutive counts, and
   Prow URLs. The browser progressively fills each card.

There is currently no shared response cache or request coalescing. See
[issue #6](https://github.com/jluhrsen/pr-ci-dashboard/issues/6).

### E2E discovery

`scripts/e2e-retest.sh --json` obtains GitHub check rollups with `gh pr view`
and the job history table from `prow.ci.openshift.org`. It identifies e2e/QE
contexts, constructs the full Prow job name from the PR base branch, and
extracts consecutive failure URLs from the history HTML.

### Payload discovery

`scripts/payload-retest.sh --json` finds payload dashboard URLs in PR comments,
downloads each dashboard page, and parses periodic jobs by CSS status class.
Entries that still look “running” are checked against their Prow
`finished.json`. Runs are sorted by payload creation timestamp before
consecutive failures are counted.

Both helpers are tightly coupled to upstream HTML and naming conventions.
Failures and parse errors are returned on the relevant card rather than being
shown as an all-green result.

### Retesting

POST `/api/retest` validates the repository, PR, and job names, applies a
per-session rate limit, and builds one PR comment:

- e2e jobs use `/test <job>`;
- payload jobs use `/payload-job <job>`.

The posting token is selected in this order:

1. connected user's GitHub OAuth token;
2. configured GitHub App installation token;
3. ambient `GH_TOKEN` or `gh` login.

If organization policy rejects a connected OAuth token and the App is
available, the request is retried as the bot and attributed to the human in a
non-command line of the comment. Actions are written to `audit_log`.

### Permafail analysis

POST `/api/jobs/analyze` runs Claude Code in print mode. POST
`/api/jobs/analyze-stream` streams Claude stdout/stderr as SSE. Both invoke the
`/ci:detect-permafail` skill with 2–10 allowlisted Prow URLs.

When a Google user is connected, their refresh token is represented as an
authorized-user ADC document in a temporary mode-0600 file. The Claude
subprocess receives that file through `GOOGLE_APPLICATION_CREDENTIALS`; the
file is deleted when the process exits, times out, or the stream is abandoned.

Results are stored per Prow URL in `job_analyses`. The streaming endpoint
currently stores error results as well, which can make an inconclusive result
look like a successful non-permafail verdict. The UI treats any non-overridden
permafail URL as a permafail for that job. Known correctness and rendering
problems are tracked in
[issue #1](https://github.com/jluhrsen/pr-ci-dashboard/issues/1) and
[issue #3](https://github.com/jluhrsen/pr-ci-dashboard/issues/3).

### Auto-retest

The `auto_retest` table stores a global enabled flag by
`owner/repo/PR-number`. Every browser loads these rows and starts its own
30-second polling interval. State transitions, cooldowns, and failure counters
live only in JavaScript memory.

This means automation needs an open browser and is not coordinated between
tabs or users. The design should move to a server-owned, idempotent coordinator
before auto-retest is used broadly in a shared deployment; see
[issue #2](https://github.com/jluhrsen/pr-ci-dashboard/issues/2).

## Backend modules

| Module | Responsibility |
| --- | --- |
| `pr_ci_dashboard/server.py` | Flask app, gates, CSRF, OAuth routes, audit, CLI, gunicorn |
| `api/search.py` | GitHub PR search and pagination transform |
| `api/jobs.py` | Parallel e2e/payload discovery |
| `api/retest.py` | Retest comment construction |
| `api/analysis.py` | Analyze/cache/override/status endpoints |
| `utils/ai_analyzer.py` | Claude process and streamed output |
| `utils/db.py` | SQLite schema and queries |
| `utils/session_store.py` | In-memory OAuth sessions and TTL |
| `utils/github_oauth.py` | GitHub device flow |
| `utils/google_oauth.py` | Google authorization code + PKCE flow |
| `utils/github_app.py` | App JWT and cached installation tokens |
| `utils/validation.py` | Subprocess/prompt boundary validation |
| `utils/rate_limit.py` | In-memory per-session sliding windows |
| `utils/job_executor.py` | Bash process execution and JSON parsing |

The legacy parser modules remain packaged but the dashboard now consumes the
helpers' JSON output directly.

## API summary

| Method and path | Purpose |
| --- | --- |
| `GET /healthz` | Process and expected SQLite schema check |
| `GET /api/csrf-token` | Session-bound CSRF token |
| `GET /api/auth/status` | Effective GitHub authentication |
| `POST /api/search` | Search and page PRs |
| `GET /api/pr/<owner>/<repo>/<number>` | Discover failed/running jobs |
| `POST /api/retest` | Post one or more retest commands |
| `POST /api/jobs/analyze` | Non-streaming permafail analysis |
| `POST /api/jobs/analyze-stream` | SSE permafail analysis |
| `GET /api/jobs/status` | Cached results by job URL |
| `POST /api/jobs/override` | Override a cached permafail |
| `POST /api/jobs/delete-cache` | Remove cached results |
| `GET /api/pr/.../permafails` | Cached permafails for one PR |
| `GET/POST /api/auto-retest` | Read or change enabled monitors |
| `GET /api/audit` | Read recent audit entries |
| `/api/github/oauth/*` | GitHub device-flow lifecycle |
| `/api/google/oauth/*` | Google login lifecycle |

All state-changing `/api` methods require the session's CSRF header. Optional
Google and GitHub gates run before endpoint handlers. CSRF prevents cross-site
browser submission; it is not authentication. The current non-loopback local
bind is tracked in
[issue #8](https://github.com/jluhrsen/pr-ci-dashboard/issues/8).

## State and concurrency

SQLite contains three tables:

- `job_analyses(job_url primary key, ...)`;
- `auto_retest(pr_key primary key, enabled, updated_at)`;
- `audit_log(id, timestamp, actor, action, target, result)`.

GitHub tokens, Google ADC dictionaries, pending device flows, rate-limit
events, and App token cache are process memory. Gunicorn therefore runs one
`gthread` worker with 16 threads. The 600-second timeout accommodates Claude
streams. Multiple workers or replicas would split session state and are not
supported.

## Trust boundaries

- Repository, PR, job, and analysis inputs are validated before reaching
  subprocesses or prompts.
- Prow analysis URLs are restricted to
  `https://prow.ci.openshift.org/view/gs/`.
- Subprocesses use argument arrays rather than `shell=True`.
- App private keys and OAuth client secrets are runtime inputs.
- OAuth session tokens are memory-only.
- Google user ADC files should live on RAM-backed `/tmp` in the container.
- Retest and analysis endpoints are rate-limited per session.

Important current limitations:

- local mode binds to all interfaces and can expose ambient credentials (#8);
- analysis output is not safely escaped in one modal (#1);
- browser-owned automation is not idempotent across users (#2);
- rate limits are process/session local, not a distributed abuse control;
- there is no TLS, Route, Ingress, or application role model in the repo.

## Deployment shape

The Containerfile installs the Python package, `gh`, Claude Code, `jq`, and the
analyzer plugin. It runs as UID 1001/GID 0 and stores data under `/data`.

The Kubernetes resources describe a single replica with a ReadWriteOnce PVC,
`Recreate` strategy, ClusterIP service, and liveness/readiness probes.
They currently require site-specific correction before deployment; see
[issue #5](https://github.com/jluhrsen/pr-ci-dashboard/issues/5).

## Verification

The repository's CI runs:

```bash
python -m pytest tests/ -v
node --check pr_ci_dashboard/static/app.js
```

Local shell syntax can be checked with:

```bash
bash -n run.sh pr_ci_dashboard/scripts/*.sh
```

The current suite has strong backend coverage for validation, CSRF, OAuth,
GitHub App tokens, the database, analysis, audit/rate limits, CLI behavior, and
health checks. It does not yet execute browser behavior or the live upstream
HTML parsing paths in CI.
