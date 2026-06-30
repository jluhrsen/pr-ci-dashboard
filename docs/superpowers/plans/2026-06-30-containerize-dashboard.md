# PR CI Dashboard Containerization - Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the PR CI Dashboard as a pip-installable Python package and containerize it for deployment to Kubernetes/OpenShift.

**Architecture:** Keep flat directory structure (no src/ migration), add pyproject.toml for pip installation, build OCI container with Claude CLI + ai-helpers plugin, deploy with standard k8s manifests (Deployment, Service, PVC) using Recreate strategy for single replica + SQLite.

**Tech Stack:** Python 3.11, Flask, pyproject.toml (PEP 621), Podman/Docker, Kubernetes, Claude Code CLI, ai-helpers plugin

**Reference:** Design spec at `docs/superpowers/specs/2026-06-30-deployment-design.md`

---

## File Structure Overview

**New files:**
- `pyproject.toml` - Python package configuration (PEP 621)
- `Containerfile` - OCI container image build
- `k8s/deployment.yaml` - Kubernetes Deployment
- `k8s/service.yaml` - Kubernetes Service
- `k8s/pvc.yaml` - PersistentVolumeClaim for database
- `.dockerignore` - Exclude unnecessary files from container build

**Modified files:**
- `server.py` - Add CLI argument parsing for port, search query
- `utils/db.py` - Already uses PR_CI_DASHBOARD_DB env var (no changes needed)
- `README.md` - Update installation and deployment instructions

**No changes needed:**
- Current flat directory structure (api/, utils/, parsers/, scripts/, static/, templates/)
- All imports work as-is (from api.search, from utils.db, etc.)

---

## Task 1: Create Python Package Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.dockerignore`

- [ ] **Step 1: Create pyproject.toml**

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

[tool.setuptools.packages.find]
where = ["."]
include = ["api*", "parsers*", "utils*"]

[tool.setuptools.package-data]
"*" = [
    "static/**/*",
    "templates/**/*",
    "scripts/**/*",
]
```

- [ ] **Step 2: Create .dockerignore**

```
__pycache__/
*.pyc
*.pyo
*.pyd
.pytest_cache/
.git/
.github/
docs/
tests/
*.md
!README.md
.gitignore
dashboard.db
.claude/
```

- [ ] **Step 3: Test local pip install**

Run: `pip install -e .`
Expected: Package installs successfully, can import modules

- [ ] **Step 4: Test server still runs**

Run: `python server.py`
Expected: Server starts on port 5000, dashboard accessible

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .dockerignore
git commit -m "feat: add Python package configuration for pip install"
```

---

## Task 2: Add CLI Argument Support

**Files:**
- Modify: `server.py` (lines 84-91, 123-129)

- [ ] **Step 1: Add argparse to server.py**

Add after imports (around line 11):

```python
import argparse
```

- [ ] **Step 2: Replace parse_cli_args function**

Replace the existing `parse_cli_args()` function (lines 84-91) with:

```python
def parse_cli_args():
    """Parse CLI arguments for port, search query, and database path."""
    global CLI_ARGS
    
    parser = argparse.ArgumentParser(
        description="PR CI Dashboard - View and retest failed OpenShift PR CI jobs"
    )
    parser.add_argument(
        '--port',
        type=int,
        default=5000,
        help='Port to run the server on (default: 5000)'
    )
    parser.add_argument(
        '--search',
        type=str,
        default='',
        help='Additional search terms to append to default query'
    )
    parser.add_argument(
        '--db-path',
        type=str,
        help='Path to SQLite database file (default: from PR_CI_DASHBOARD_DB env or ~/.local/share/pr-ci-dashboard/dashboard.db)'
    )
    
    args = parser.parse_args()
    
    # Set CLI_ARGS for search (keep existing behavior)
    if args.search:
        CLI_ARGS = [args.search]
    else:
        CLI_ARGS = []
    
    return args
```

- [ ] **Step 3: Update main() to use parsed args**

Replace the main() function (lines 92-129) with:

```python
def main():
    """Start the Flask server."""
    print("🚀 PR CI Dashboard Starting...")

    # Parse CLI arguments
    args = parse_cli_args()
    
    # Override DB path if specified
    if args.db_path:
        import os
        os.environ['PR_CI_DASHBOARD_DB'] = args.db_path
        print(f"📁 Using database: {args.db_path}")

    # Initialize database
    try:
        init_db()
        print("✅ Database initialized")
    except Exception as e:
        print(f"❌ Failed to initialize database: {e}")
        print("Cannot start dashboard without database.")
        sys.exit(1)

    # Fetch scripts from GitHub
    try:
        fetch_scripts()
    except Exception as e:
        print(f"❌ Failed to fetch scripts: {e}")
        print("Cannot start dashboard without scripts.")
        sys.exit(1)

    # Check gh auth
    auth = check_gh_auth()
    if not auth["authenticated"]:
        print(f"⚠️  {auth['error']}")
        print("Dashboard will start but retest buttons will be disabled.")
    else:
        print("✅ GitHub CLI authenticated")

    print(f"\n🌐 Dashboard running at http://localhost:{args.port}")
    print(f"📝 Default search: {DEFAULT_QUERY}")
    if CLI_ARGS:
        print(f"   + Additional: {' '.join(CLI_ARGS)}")

    app.run(host='0.0.0.0', port=args.port, debug=True)
```

- [ ] **Step 4: Test CLI arguments**

Run: `python server.py --port 8080 --search "repo:ovn-kubernetes"`
Expected: Server starts on port 8080, search includes repo filter

Run: `python server.py --help`
Expected: Shows help with all arguments

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "feat: add CLI arguments for port, search, and db-path"
```

---

## Task 3: Create Container Image

**Files:**
- Create: `Containerfile`

- [ ] **Step 1: Create Containerfile**

```dockerfile
FROM registry.ci.openshift.org/ocp/builder:rhel-9-golang-1.25-openshift-4.22

# Install system dependencies
RUN dnf install -y \
    claude-code \
    gh \
    python3.11 \
    python3.11-pip \
    && dnf clean all && \
    alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 && \
    alternatives --set python3 /usr/bin/python3.11

# Create claude user FIRST (OpenShift compatibility - random UID in root group)
RUN useradd -m -u 1000 -g 0 -s /bin/bash claude && \
    mkdir -p /home/claude/.claude /data && \
    chown -R claude:root /home/claude /data && \
    chmod -R g+rwx /home/claude /data

# Copy dashboard code
COPY . /app
WORKDIR /app

# Install dashboard package
RUN pip3.11 install --no-cache-dir /app

# Switch to claude user
USER claude

# Configure Claude for Vertex AI
ENV CLAUDE_CODE_USE_VERTEX=1
ENV ANTHROPIC_VERTEX_PROJECT_ID=<internal-vertex-project>
ENV HOME=/home/claude

# Install ai-helpers plugin as claude user
RUN claude plugin install ci@ai-helpers

# Set database path for container deployment
ENV PR_CI_DASHBOARD_DB=/data/dashboard.db

# Expose port
EXPOSE 5000

# Run server
ENTRYPOINT ["python3.11", "server.py"]
CMD ["--port", "5000"]
```

- [ ] **Step 2: Build container image**

Run: `podman build -t pr-ci-dashboard:test .`
Expected: Image builds successfully

- [ ] **Step 3: Test container locally (will fail without secrets - expected)**

Run: `podman run -p 5000:5000 pr-ci-dashboard:test`
Expected: Server starts but may show warnings about missing GCP credentials or gh auth

- [ ] **Step 4: Stop test container**

Run: `podman ps` to find container ID, then `podman stop <id>`

- [ ] **Step 5: Commit**

```bash
git add Containerfile
git commit -m "feat: add Containerfile for building dashboard container image"
```

---

## Task 4: Create Kubernetes Manifests

**Files:**
- Create: `k8s/pvc.yaml`
- Create: `k8s/deployment.yaml`
- Create: `k8s/service.yaml`

- [ ] **Step 1: Create k8s directory and PVC manifest**

```bash
mkdir -p k8s
```

Create `k8s/pvc.yaml`:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: dashboard-data
  labels:
    app: pr-ci-dashboard
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 5Gi
```

- [ ] **Step 2: Create Deployment manifest**

Create `k8s/deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pr-ci-dashboard
  labels:
    app: pr-ci-dashboard
spec:
  replicas: 1
  strategy:
    type: Recreate  # Required for single-pod + RWO PVC
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
        image: pr-ci-dashboard:latest
        imagePullPolicy: IfNotPresent
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
          optional: true
```

- [ ] **Step 3: Create Service manifest**

Create `k8s/service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: pr-ci-dashboard
  labels:
    app: pr-ci-dashboard
spec:
  selector:
    app: pr-ci-dashboard
  ports:
  - port: 80
    targetPort: 5000
    name: http
  type: ClusterIP
```

- [ ] **Step 4: Validate manifests**

Run: `kubectl apply --dry-run=client -f k8s/`
Expected: No errors (may show "created (dry run)")

- [ ] **Step 5: Commit**

```bash
git add k8s/
git commit -m "feat: add Kubernetes deployment manifests"
```

---

## Task 5: Update Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add containerization section to README**

Add after the "Manual Installation" section (around line 85):

```markdown
## Container Deployment

### Build Container Image

```bash
podman build -t pr-ci-dashboard:latest .
```

### Run Container Locally

```bash
# Create volume for database persistence
podman volume create dashboard-data

# Run container
podman run -d \
  --name pr-ci-dashboard \
  -p 5000:5000 \
  -v dashboard-data:/data \
  pr-ci-dashboard:latest

# Access at http://localhost:5000
```

### Deploy to Kubernetes

**Prerequisites:**
- kubectl configured for target cluster
- GCP service account JSON for Vertex AI (optional - required for permafail detection)
- GitHub credentials (PAT or app token) mounted as secret

**Deploy:**

```bash
# Create namespace
kubectl create namespace pr-ci-dashboard

# Create GCP credentials secret (if available)
kubectl create secret generic gcp-service-account \
  --from-file=gcp-sa.json=/path/to/service-account.json \
  -n pr-ci-dashboard

# Deploy application
kubectl apply -f k8s/ -n pr-ci-dashboard

# Port-forward to access locally
kubectl port-forward svc/pr-ci-dashboard 5000:80 -n pr-ci-dashboard
```

Access at http://localhost:5000

**Deployment details:** See `docs/superpowers/specs/2026-06-30-deployment-design.md`
```

- [ ] **Step 2: Update Quick Start to mention pip install**

Replace the "Quick Start" section (lines 7-15) with:

```markdown
## Quick Start

**Option 1: One-liner (curl | sh):**
```bash
curl -fsSL https://raw.githubusercontent.com/jluhrsen/pr-ci-dashboard/main/run.sh | sh
```

**Option 2: Pip install:**
```bash
pip install git+https://github.com/jluhrsen/pr-ci-dashboard.git
python server.py
```

Then open **http://localhost:5000**
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add container and Kubernetes deployment instructions"
```

---

## Task 6: Test KIND Deployment

**Files:**
- None (testing only)

- [ ] **Step 1: Create KIND cluster**

Run: `kind create cluster --name dashboard-test`
Expected: Cluster created successfully

- [ ] **Step 2: Load image into KIND**

Run: `kind load docker-image pr-ci-dashboard:test --name dashboard-test`
Expected: Image loaded into cluster

- [ ] **Step 3: Update deployment to use test image tag**

Run: `sed -i 's/pr-ci-dashboard:latest/pr-ci-dashboard:test/' k8s/deployment.yaml`

- [ ] **Step 4: Deploy to KIND**

Run: `kubectl apply -f k8s/`
Expected: All resources created

Run: `kubectl get pods`
Expected: pr-ci-dashboard pod running (may take 1-2 minutes)

- [ ] **Step 5: Check pod logs for startup**

Run: `kubectl logs -l app=pr-ci-dashboard --tail=50`
Expected: See "Dashboard running at http://localhost:5000" message
May see warnings about missing GCP credentials or gh auth (expected)

- [ ] **Step 6: Port-forward and access dashboard**

Run: `kubectl port-forward svc/pr-ci-dashboard 5000:80`
In browser: Open http://localhost:5000
Expected: Dashboard loads (search may fail without gh auth)

- [ ] **Step 7: Test database persistence**

Run: `kubectl delete pod -l app=pr-ci-dashboard`
Wait for new pod to start: `kubectl get pods`
Verify dashboard still works: port-forward and access again

- [ ] **Step 8: Cleanup**

Run: `kind delete cluster --name dashboard-test`

- [ ] **Step 9: Restore deployment manifest**

Run: `git checkout k8s/deployment.yaml`

- [ ] **Step 10: Document test results**

Create a test summary in your notes:
- Container builds: ✅/❌
- KIND deployment: ✅/❌
- Database persistence: ✅/❌
- Any errors or warnings encountered

---

## Task 7: Create Deployment Guide

**Files:**
- Create: `docs/deployment-guide.md`

- [ ] **Step 1: Create deployment guide**

```markdown
# PR CI Dashboard - Deployment Guide

## Phase 1: Local Development

### Prerequisites
- Python 3.8+
- GitHub CLI authenticated (`gh auth login`)
- Claude Code CLI (optional - for permafail detection)

### Installation

```bash
# Clone repository
git clone https://github.com/jluhrsen/pr-ci-dashboard.git
cd pr-ci-dashboard

# Install package
pip install -e .

# Run server
python server.py
```

Access at http://localhost:5000

## Phase 1: Container Testing

### Prerequisites
- Podman or Docker
- KIND (for Kubernetes testing)

### Build and Test

```bash
# Build image
podman build -t pr-ci-dashboard:test .

# Run locally
podman run -p 5000:5000 -v dashboard-data:/data pr-ci-dashboard:test

# Test in KIND
kind create cluster --name dashboard-test
kind load docker-image pr-ci-dashboard:test --name dashboard-test
kubectl apply -f k8s/
kubectl port-forward svc/pr-ci-dashboard 5000:80
```

## Phase 2: Production Deployment (TODO)

### Prerequisites
- Access to production OpenShift/Kubernetes cluster
- GCP service account for Vertex AI
- GitHub App or bot account credentials
- Domain/Ingress configuration

### Steps
1. Build and push image to quay.io
2. Create production secrets (GCP, GitHub)
3. Deploy with production manifests
4. Configure Ingress/Route for external access
5. Set up monitoring/alerting

**Note:** Production deployment details TBD - coordinate with infrastructure team.

## Troubleshooting

### Container won't start
- Check logs: `kubectl logs -l app=pr-ci-dashboard`
- Verify secrets are mounted: `kubectl describe pod -l app=pr-ci-dashboard`

### Dashboard loads but search fails
- Check GitHub CLI auth: Container needs GH_TOKEN env var or gh auth
- For local: `gh auth status`
- For container: Mount GitHub credentials as secret

### Permafail detection not working
- Requires Claude Code CLI and ai-helpers plugin
- Check logs for "Failed to install plugin" errors
- Verify GCP service account is mounted and GOOGLE_APPLICATION_CREDENTIALS is set

### Database doesn't persist
- Verify PVC is mounted: `kubectl get pvc`
- Check database path: `kubectl exec -it <pod> -- ls -la /data/`
```

- [ ] **Step 2: Commit**

```bash
git add docs/deployment-guide.md
git commit -m "docs: add deployment guide for local, container, and kubernetes"
```

---

## Success Criteria

**Phase 1 Complete When:**

- [ ] Package is pip-installable (`pip install -e .` works)
- [ ] Container image builds successfully
- [ ] Deployment to KIND cluster succeeds
- [ ] Dashboard accessible via port-forward
- [ ] Database persists across pod restarts
- [ ] Pod logs show clean startup (warnings about missing auth OK)
- [ ] Documentation updated with container/k8s instructions

**Known Limitations (deferred to Phase 2):**
- No production secrets configured (GCP, GitHub)
- No Ingress/Route (port-forward access only)
- No automated image builds
- Single replica only (Recreate strategy = 30-60s downtime on updates)

---

## Next Steps (Phase 2)

After Phase 1 is complete and validated:

1. **Find permanent cluster** - Coordinate with team for production cluster access
2. **Set up secrets** - Work with vault/infra team for GCP and GitHub credentials
3. **Image registry** - Push images to quay.io with proper tags
4. **Ingress/Route** - Configure production URL (e.g., pr-ci-dashboard.ci.openshift.org)
5. **Monitoring** - Add health checks, metrics, alerting
6. **GitOps** - Set up auto-rebuild on ai-helpers updates

See design spec for full Phase 2/3 roadmap.
