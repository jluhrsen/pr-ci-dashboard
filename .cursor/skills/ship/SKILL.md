---
name: ship
description: >-
  Commit, push, build, and redeploy the pr-ci-dashboard container to quay.io and
  restart the local flake-buster instance. Use when the user says "ship", "ship it",
  or asks to deploy this dashboard to the local container.
disable-model-invocation: true
---

# Ship pr-ci-dashboard

Deploy changes from this repo to the local `flake-buster` container.

## Prerequisites

- Changes are tested and ready to release
- `~/.config/fb.env` exists with runtime secrets
- Podman secret `fb-github-app-key` is configured
- Volume `fb-data` exists (created automatically on first run)
- `podman login quay.io` (required for push)
- `CI_REGISTRY_TOKEN` + `podman login registry.ci.openshift.org` (required for full `Containerfile` rebuild)

## Workflow

Run these steps in order. Do not skip steps.

### 1. Commit and push

```bash
git status
git diff
git log -3 --oneline
```

Stage relevant changes, commit with a concise message, then push:

```bash
git add <files>
git commit -m "$(cat <<'EOF'
Your commit message here.

EOF
)"
git push
```

### 2. Build and push image

From the repository root, try the full image build first:

```bash
podman build -f Containerfile -t quay.io/jluhrsen/pr-ci-dashboard:latest .
```

If the OpenShift builder base image is unavailable (registry auth), use an incremental rebuild from the published image instead:

```bash
podman build -f - -t quay.io/jluhrsen/pr-ci-dashboard:latest . <<'EOF'
FROM quay.io/jluhrsen/pr-ci-dashboard:latest
USER 0
WORKDIR /app
COPY . /app
RUN pip3.11 install --no-cache-dir /app
USER 1001
WORKDIR /home/claude
EOF
```

Push to Quay:

```bash
podman push quay.io/jluhrsen/pr-ci-dashboard:latest
```

### 3. Pull latest image

```bash
podman pull quay.io/jluhrsen/pr-ci-dashboard:latest
```

Skip this step when `podman push` failed but the local build succeeded; use the locally tagged image for step 4.

### 4. Replace running container

```bash
podman rm -f flake-buster
podman run -d --name flake-buster -p 127.0.0.1:8181:5000 --env-file ~/.config/fb.env -v fb-data:/data --secret source=fb-github-app-key,type=mount,target=/secrets/github-app/private-key.pem,uid=1001,gid=0,mode=0400 quay.io/jluhrsen/pr-ci-dashboard:latest
```

### 5. Verify

```bash
podman ps --filter name=flake-buster
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8181/healthz
```

Expect HTTP 200 from `/healthz`.

## Notes

- Image tag is always `latest`; `podman pull` before `run` ensures fresh bits.
- `fb-data` volume persists SQLite state across redeploys.
- Dashboard is at http://localhost:8181/
