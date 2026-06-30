---
title: Containerized Deployment for PR CI Dashboard
date: 2026-06-30
status: approved
---

# Containerized Deployment for PR CI Dashboard

## Overview

Transform the PR CI Dashboard from a local curl-and-run script into a professionally packaged, containerized application deployable to Kubernetes/OpenShift. Enable shared multi-user deployment while maintaining portability across environments (KIND, OpenShift, vanilla k8s, standalone containers).

## Goals

1. **Team ownership** - OpenShift team can deploy, maintain, and control the dashboard
2. **Sustainability** - Long-term maintainable deployment strategy
3. **Shared deployment** - Single running instance accessible to multiple users (shared permafail cache)
4. **Portability** - Works on any container runtime or Kubernetes distribution
5. **Professional packaging** - Pip-installable for local development

**Note:** Full shared state (auto-retest tracking in database) is deferred to Phase 3 tech debt. Phase 1 focuses on containerization and shared permafail detection cache.

## Non-Goals (Phase 1)

- Authentication/authorization (start open, add later if needed)
- Sippy integration (explore after standalone deployment works)
- Multi-replica scaling (SQLite limits to 1 replica initially)
- GitOps auto-deployment (manual deployment first)
- Production DNS setup (use port-forward or default cluster domain initially)

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    User Browser                          │
│              http://localhost:5000 or                    │
│         https://dashboard.apps.cluster.com               │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
          ┌──────────────────────┐
          │  Kubernetes Service  │
          │    (port 80 → 5000)  │
          └──────────┬───────────┘
                     │
                     ▼
          ┌──────────────────────────────────┐
          │     Pod: pr-ci-dashboard         │
          │  ┌────────────────────────────┐  │
          │  │  Container                 │  │
          │  │  - Flask app (port 5000)   │  │
          │  │  - Claude Code CLI         │  │
          │  │  - gh CLI                  │  │
          │  │  - Python 3.11             │  │
          │  └────────────────────────────┘  │
          │                                  │
          │  Mounts:                         │
          │  - /data (PVC) → dashboard.db    │
          │  - /secrets/gcp-sa.json          │
          │  - /secrets/github-app (future)  │
          └──────────────────────────────────┘
                     │
                     ▼
          ┌──────────────────────┐
          │  PersistentVolume    │
          │   dashboard.db       │
          │   (5Gi, RWO)         │
          └──────────────────────┘
```

## Component Design

### 1. Python Package Structure

**Goals:**
- Pip-installable for local development
- Bundled correctly for container builds
- Follows modern Python packaging standards (PEP 621)

**Directory structure:**

```
pr-ci-dashboard/
├── pyproject.toml           # Modern packaging (PEP 621)
├── server.py                # Flask app (top-level, imports from api/, utils/, parsers/)
├── api/                     # API endpoints (keep current structure)
│   ├── search.py
│   ├── jobs.py
│   ├── retest.py
│   └── analysis.py
├── parsers/                 # Keep current structure
├── utils/                   # Keep current structure
│   ├── ai_analyzer.py
│   ├── script_fetcher.py
│   ├── gh_auth.py
│   └── db.py
├── scripts/                 # Bash scripts (keep at top level)
├── static/
│   ├── app.js
│   └── styles.css
├── templates/
│   └── index.html
├── tests/
├── Containerfile
├── k8s/
│   ├── deployment.yaml
│   ├── service.yaml
│   └── pvc.yaml
└── README.md
```

**Note:** No `src/` migration - keep current flat structure to avoid import rewrites.

**pyproject.toml:**

```toml
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "pr-ci-dashboard"
version = "0.1.0"
description = "Dashboard for viewing and retesting failed OpenShift PR CI jobs"
authors = [{name = "Jamo Luhrsen", email = "jluhrsen@redhat.com"}]
readme = "README.md"
license = {text = "Apache-2.0"}
requires-python = ">=3.8"
dependencies = [
    "flask>=2.0.0",
    "requests>=2.28.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
]

[project.scripts]
pr-ci-dashboard = "pr_ci_dashboard.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
"*" = [
    "static/**/*",
    "templates/**/*",
    "scripts/**/*",
]
```

**Installation methods:**

```bash
# Local development (editable install)
pip install -e .

# From source
pip install .

# From git (future)
pip install git+https://github.com/jluhrsen/pr-ci-dashboard.git
```

**CLI usage:**

```bash
# Default search
pr-ci-dashboard
# Searches: "is:pr is:open archived:false author:openshift-pr-manager[bot]"

# Append to default search
pr-ci-dashboard --search "repo:ovn-kubernetes"
# Searches: "is:pr is:open archived:false author:openshift-pr-manager[bot] repo:ovn-kubernetes"

# Override default search completely
pr-ci-dashboard --search-override "author:jluhrsen is:draft"

# Custom port
pr-ci-dashboard --port 8080

# Custom database location
pr-ci-dashboard --db-path /custom/path.db
```

**What the CLI does:**
1. Starts Flask web server on specified port
2. Sets default search query
3. Prints: "🌐 Dashboard running at http://localhost:5000"
4. User opens browser to interact with web UI

### 2. Container Image

**Goals:**
- Bundle Claude Code CLI (same pattern as ai-helpers repo)
- Install dashboard as Python package
- Support both local testing and production deployment
- Work on any OCI-compliant runtime (podman, docker, containerd)

**Containerfile:**

```dockerfile
FROM registry.ci.openshift.org/ocp/builder:rhel-9-golang-1.25-openshift-4.22

# Install system dependencies
RUN dnf install -y \
    claude-code \
    gh \
    python3.11 \
    python3.11-pip \
    && dnf clean all

# Install dashboard as Python package
COPY . /app
RUN pip3.11 install /app

# Create claude user FIRST (OpenShift compatibility - random UID in root group)
RUN useradd -m -u 1000 -g 0 -s /bin/bash claude && \
    mkdir -p /home/claude/.claude /data && \
    chown -R claude:root /home/claude /data && \
    chmod -R g+rwx /home/claude /data

USER claude
WORKDIR /app

# Configure Claude for Vertex AI
ENV CLAUDE_CODE_USE_VERTEX=1
ENV ANTHROPIC_VERTEX_PROJECT_ID=<internal-vertex-project>
ENV HOME=/home/claude

# Install ai-helpers plugin as claude user
RUN claude plugin install ci@ai-helpers

# Mount points:
# - /secrets/gcp-sa.json for GOOGLE_APPLICATION_CREDENTIALS
# - /data for persistent SQLite database

ENV PR_CI_DASHBOARD_DB=/data/dashboard.db

ENTRYPOINT ["python", "server.py"]
```

**Build and run:**

```bash
# Build image
podman build -t pr-ci-dashboard:latest .

# Run locally for testing
podman run -p 5000:5000 -v dashboard-data:/data pr-ci-dashboard:latest

# Access at http://localhost:5000
```

**Why keep Claude CLI (not migrate to API):**
- Skills (like `/ci:detect-permafail`) work out of the box
- Tool execution (Bash, WebFetch, Read) already implemented
- ai-helpers repo provides working Containerfile pattern
- Simpler than reimplementing tool calling layer
- No code changes needed to `utils/ai_analyzer.py`

### 3. AI Analysis with Claude Code CLI

**Current implementation:** `utils/ai_analyzer.py` spawns `claude` subprocess and parses stdout - **NO CHANGES NEEDED**

**Container setup:**
- Claude Code installed via RPM (line 6 in Containerfile)
- ai-helpers plugin installed at build time (line 21)
- Vertex AI credentials via mounted service account JSON
- Environment variables configure Vertex backend

**Authentication flow:**
```
Dashboard → claude subprocess → Vertex AI (via GOOGLE_APPLICATION_CREDENTIALS)
                                          ↓
                                   GCP Project: <internal-vertex-project>
```

**Skills availability:**
- `/ci:detect-permafail` from ai-helpers plugin
- Installed at image build time: `claude plugin install ci@ai-helpers`
- Updates require image rebuild (see Update Strategy below)

### 4. Database & Configuration Management

**Database location strategy:**

**Local development:**
```
~/.local/share/pr-ci-dashboard/
  └── dashboard.db
```

**Container deployment:**
```
/data/
  └── dashboard.db
```

**Implementation:**

```python
# server.py
from pathlib import Path
import os

def get_data_dir():
    """Get data directory based on environment"""
    # Container deployment
    if os.path.exists('/data'):
        return Path('/data')
    
    # Local development (XDG pattern)
    data_dir = Path.home() / '.local' / 'share' / 'pr-ci-dashboard'
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

# Configure database
DATA_DIR = get_data_dir()
DB_PATH = DATA_DIR / 'dashboard.db'
app.config['DB_PATH'] = str(DB_PATH)
```

**Database persistence:**
- Mounted on PersistentVolumeClaim (5Gi, ReadWriteOnce)
- Survives pod restarts/updates
- Shared across all users accessing the dashboard

**Environment variable overrides:**
- `DASHBOARD_PORT` (default: 5000)
- `DASHBOARD_DB_PATH` (override default location)
- `DASHBOARD_SEARCH_QUERY` (default search)

### 5. Kubernetes Deployment

**Goals:**
- Standard k8s primitives (no OpenShift-specific features)
- Single replica initially (SQLite concurrency limitation)
- Zero-downtime rolling updates
- Persistent storage for database

**deployment.yaml:**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pr-ci-dashboard
  labels:
    app: pr-ci-dashboard
spec:
  replicas: 1  # Single replica (SQLite + RWO PVC limitation)
  strategy:
    type: Recreate  # Required for single-pod + RWO PVC; no zero-downtime until PostgreSQL
  selector:
    matchLabels:
      app: pr-ci-dashboard
  template:
    metadata:
      labels:
        app: pr-ci-dashboard
    spec:
      containers:
      - name: dashboard
        image: quay.io/openshift/pr-ci-dashboard:latest
        ports:
        - containerPort: 5000
          name: http
        env:
        - name: CLAUDE_CODE_USE_VERTEX
          value: "1"
        - name: ANTHROPIC_VERTEX_PROJECT_ID
          value: <internal-vertex-project>
        - name: GOOGLE_APPLICATION_CREDENTIALS
          value: /secrets/gcp-sa.json
        - name: PR_CI_DASHBOARD_DB
          value: /data/dashboard.db
        volumeMounts:
        - name: data
          mountPath: /data
        - name: gcp-credentials
          mountPath: /secrets
          readOnly: true
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "2000m"
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: dashboard-data
      - name: gcp-credentials
        secret:
          secretName: gcp-service-account
```

**pvc.yaml:**

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: dashboard-data
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 5Gi
```

**service.yaml:**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: pr-ci-dashboard
spec:
  selector:
    app: pr-ci-dashboard
  ports:
  - port: 80
    targetPort: 5000
    name: http
  type: ClusterIP
```

**Deployment commands:**

```bash
# Create namespace
kubectl create namespace pr-ci-dashboard

# Create secrets (see Authentication section)
kubectl create secret generic gcp-service-account \
  --from-file=gcp-sa.json=/path/to/service-account-key.json \
  -n pr-ci-dashboard

# Deploy
kubectl apply -f k8s/pvc.yaml -n pr-ci-dashboard
kubectl apply -f k8s/deployment.yaml -n pr-ci-dashboard
kubectl apply -f k8s/service.yaml -n pr-ci-dashboard

# Access via port-forward (Phase 1)
kubectl port-forward svc/pr-ci-dashboard 5000:80 -n pr-ci-dashboard
# Open http://localhost:5000
```

### 6. Update Strategy for ai-helpers Plugin

**Challenge:** Dashboard depends on `/ci:detect-permafail` skill from ai-helpers repo. How do we update when ai-helpers changes?

**Chosen approach: Option C - Auto-rebuild with GitOps (phased implementation)**

**Phase 1: Manual rebuild**
- ai-helpers baked into container image at build time
- To update: rebuild image manually, redeploy
- Simple, predictable, version-controlled

**Phase 2: Automated rebuild**
- GitHub Action or Tekton pipeline watches ai-helpers repo
- Auto-rebuilds dashboard image when ai-helpers commits
- Pushes new image tag to quay.io
- GitOps (ArgoCD/Flux) auto-deploys new image

**What persists across updates:**
- ✅ Database (permafail cache, analysis results) - on PVC
- ✅ Persistent data written to `/data`
- ❌ Active browser sessions - users refresh
- ❌ In-progress AI analysis - restarts on next check

**Update behavior (Recreate strategy):**
```
1. Old pod terminates (brief downtime starts)
2. PVC unmounts from old pod
3. New pod starts with updated ai-helpers
4. PVC mounts to new pod (same database)
5. New pod becomes ready (downtime ends)
```

**Downtime:** ~30-60 seconds during pod replacement. Zero-downtime updates require PostgreSQL + multi-replica (Phase 3).

**No data loss** - the database lives on a PersistentVolume separate from the container filesystem.

### 7. Authentication

**Two auth requirements:**
1. Vertex AI (for Claude Code permafail detection)
2. GitHub (for posting retest comments)

#### 7.1 Vertex AI Authentication

**Setup:**
- GCP service account with Vertex AI access
- Service account key JSON mounted as Secret
- Claude Code configured to use Vertex backend

**Environment variables:**
```bash
CLAUDE_CODE_USE_VERTEX=1
ANTHROPIC_VERTEX_PROJECT_ID=<internal-vertex-project>
GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-sa.json
```

**Secret creation:**
```bash
kubectl create secret generic gcp-service-account \
  --from-file=gcp-sa.json=/path/to/service-account-key.json
```

#### 7.2 GitHub Authentication

**Requirement:** Dashboard needs GitHub credentials for:
1. **PR search** - `gh search prs` in api/search.py
2. **Job status** - bash scripts that query GitHub API
3. **Retest comments** - posting `/test` and `/payload-job` comments

**Critical:** ALL dashboard features require GitHub auth, not just retest.

**Options identified:**
- Use existing openshift-pr-manager app (ID: 1460951) - need vault access coordination
- Create dashboard-specific GitHub App - own the credentials
- Bot account PAT - quick but less secure
- Personal token - ONLY for local development testing

**Phase 1 approach:** TBD during implementation
- Local dev: Use personal `gh auth` credentials
- Container testing: Manually mount a test PAT or personal token as secret
- Production setup deferred to Phase 2

**Phase 2 production:** 
- Coordinate with vault/infra team for proper secret management
- Configure GitHub App credentials (openshift-pr-manager or dashboard-specific)
- Set up proper token rotation/renewal

**Why this complexity:** Container needs server-side credentials, not personal laptop `gh auth` session.

## Deployment Phases

### Phase 1: Containerize & Deploy to Test Environment

**Goals:**
- Prove the containerization approach works
- Test with KIND or temporary OpenShift cluster
- Validate database persistence across pod restarts

**Deliverables:**
1. Create pip package (pyproject.toml, src/ layout)
2. Build container image with Claude CLI + ai-helpers
3. Create k8s manifests (Deployment, Service, PVC)
4. Test deployment to KIND cluster locally
5. Test deployment to temporary OpenShift cluster
6. Validate:
   - Dashboard serves web UI
   - Permafail detection works (Claude CLI + Vertex AI)
   - Database persists across pod restarts
   - Can rebuild image and rolling-update without data loss

**Access method:** kubectl port-forward (no Ingress/Route yet)

**Auth approach:** Manual secrets for testing, defer production auth setup

### Phase 2: Production Deployment

**Goals:**
- Deploy to permanent cluster
- Production-grade authentication
- Clean URL with Ingress/Route

**Tasks:**
1. Find permanent cluster home (coordinate with team)
2. Set up production GCP service account for Vertex AI
3. Configure GitHub authentication (app or vault integration)
4. Create Ingress or OpenShift Route for clean URL
5. Push images to quay.io (not local builds)
6. Document production deployment process

**Access method:** https://pr-ci-dashboard.ci.openshift.org (or similar)

### Phase 3: Automation & Polish

**Goals:**
- GitOps auto-deployment
- Multi-user shared state improvements
- Scale beyond single replica

**Tasks:**
1. GitOps: Auto-rebuild image when ai-helpers updates
2. Auto-deploy with ArgoCD/Flux
3. Migrate auto-retest state from localStorage to database
4. Add PostgreSQL support for multi-replica scaling
5. Monitoring/alerting setup

## Technical Debt Items

Things we're deferring to later phases:

1. **Move auto-retest state to database**
   - **Current:** Browser localStorage (per-browser, not shared)
   - **Desired:** Server-side database (shared source of truth)
   - **Why:** Multiple users should see the same auto-retest state
   - **Schema:**
     ```sql
     CREATE TABLE auto_retest_state (
         pr_key TEXT PRIMARY KEY,  -- "owner/repo/number"
         enabled INTEGER NOT NULL DEFAULT 1,
         updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
     );
     
     CREATE TABLE job_failure_counters (
         job_key TEXT PRIMARY KEY,  -- "owner/repo/number/jobName"
         consecutive_failures INTEGER NOT NULL DEFAULT 0,
         last_state TEXT,  -- 'success', 'failure', 'pending'
         last_retest_at TIMESTAMP,
         updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
     );
     ```

2. **Multi-replica support**
   - **Current:** Single replica (SQLite concurrency limitation)
   - **Desired:** Multiple replicas for HA and load distribution
   - **Blockers:** SQLite doesn't handle concurrent writes well
   - **Solutions:** Migrate to PostgreSQL or implement leader election

3. **Production DNS/Ingress**
   - **Current:** kubectl port-forward or cluster default domain
   - **Desired:** Clean URL like https://pr-ci-dashboard.ci.openshift.org
   - **Blocker:** Need to coordinate with Red Hat infra or use existing CI cluster DNS

4. **GitHub App authentication**
   - **Current:** Deferred to implementation
   - **Desired:** Proper GitHub App credentials via vault
   - **Options:** Use openshift-pr-manager app or create dashboard-specific app

5. **GitOps auto-deployment**
   - **Current:** Manual `kubectl apply` or `kubectl set image`
   - **Desired:** Push to main → auto-rebuild → auto-deploy
   - **Tools:** GitHub Actions + Tekton/ArgoCD/Flux

6. **Sippy integration investigation**
   - **Current:** Standalone dashboard
   - **Future:** Explore embedding in Sippy as tab/module
   - **Blocker:** Need to investigate Sippy architecture first

## Testing Plan

### Local Testing (Developer Laptop)

```bash
# Install package in dev mode
pip install -e .

# Run locally
pr-ci-dashboard

# Access at http://localhost:5000
```

### Container Testing (Podman/Docker)

```bash
# Build image
podman build -t pr-ci-dashboard:test .

# Run with volume for database persistence
podman run -p 5000:5000 \
  -v dashboard-data:/data \
  -e ANTHROPIC_VERTEX_PROJECT_ID=<internal-vertex-project> \
  -v /path/to/gcp-sa.json:/secrets/gcp-sa.json:ro \
  -e GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-sa.json \
  pr-ci-dashboard:test

# Test:
# 1. Dashboard loads at http://localhost:5000
# 2. Search returns PRs
# 3. Permafail detection works (trigger on 3rd consecutive failure)
# 4. Stop container, restart → database persists
```

### KIND Testing (Local Kubernetes)

```bash
# Create KIND cluster
kind create cluster --name dashboard-test

# Load image into cluster
kind load docker-image pr-ci-dashboard:test --name dashboard-test

# Deploy
kubectl apply -f k8s/

# Port-forward
kubectl port-forward svc/pr-ci-dashboard 5000:80

# Test:
# 1. Dashboard accessible at http://localhost:5000
# 2. Permafail detection works
# 3. Rolling update: edit deployment, verify zero downtime
# 4. Delete pod, verify database persists
```

### OpenShift Testing (Temporary Cluster)

```bash
# Create temporary cluster (if available)
# or use existing test cluster

# Deploy
oc new-project pr-ci-dashboard
oc apply -f k8s/

# Create OpenShift Route (instead of kubectl port-forward)
oc expose svc/pr-ci-dashboard

# Get URL
oc get route pr-ci-dashboard -o jsonpath='{.spec.host}'

# Test same scenarios as KIND
```

## Success Criteria

**Phase 1 complete when:**
- [ ] Dashboard pip-installable and runs locally
- [ ] Container image builds successfully
- [ ] Deploys to KIND cluster
- [ ] Deploys to temporary OpenShift cluster
- [ ] Permafail detection works in container (Claude CLI + Vertex AI)
- [ ] Database persists across pod restarts
- [ ] Rolling updates work without data loss
- [ ] Multiple users can access shared dashboard (port-forward)

**Phase 2 complete when:**
- [ ] Deployed to permanent cluster
- [ ] Production Vertex AI authentication configured
- [ ] GitHub authentication working for retest
- [ ] Clean URL configured (Ingress/Route)
- [ ] Images hosted on quay.io
- [ ] Documentation for production deployment

**Phase 3 complete when:**
- [ ] GitOps auto-rebuild on ai-helpers updates
- [ ] GitOps auto-deploy to cluster
- [ ] Auto-retest state in database (shared)
- [ ] Multi-replica deployment with PostgreSQL

## Security Considerations

1. **Secrets management:**
   - GCP service account key stored as Kubernetes Secret (not in git)
   - GitHub credentials stored as Secret (not in git)
   - No credentials in container image

2. **Container security:**
   - Non-root user (UID 1000, GID 0 for OpenShift compatibility)
   - Read-only secret mounts
   - Minimal base image (RHEL-based builder image)

3. **Network security:**
   - ClusterIP Service (not exposed outside cluster by default)
   - Ingress/Route adds controlled external access
   - No authentication in Phase 1 (add if needed)

4. **Database security:**
   - SQLite file on PVC (mode 0600 in future)
   - No sensitive data stored (just permafail analysis cache)
   - PRs and job data are public (from GitHub API)

## Future Enhancements

- **Real-time updates:** WebSocket for live job status changes (not polling)
- **Notification system:** Slack/email when permafail detected
- **Analytics dashboard:** Charts for permafail trends over time
- **Query builder:** UI for building GitHub search queries
- **Saved searches:** Bookmark common queries
- **Job history:** Track permafail analysis history per job
- **API mode:** REST API for programmatic access
- **Sippy integration:** Embed dashboard in Sippy UI

## References

- Current dashboard: https://github.com/jluhrsen/pr-ci-dashboard
- ai-helpers repo: `../ai-helpers/`
- ai-helpers Containerfile: `../ai-helpers/images/Dockerfile`
- openshift-pr-manager app: https://github.com/settings/apps/openshift-pr-manager
- Multi-user deployment doc: `docs/multi-user-deployment.md`
