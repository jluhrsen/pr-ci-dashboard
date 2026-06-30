# PR CI Dashboard Container Image
# Bundles the pip-installable dashboard package with Claude Code CLI and ai-helpers plugin

FROM registry.ci.openshift.org/ocp/builder:rhel-9-golang-1.25-openshift-4.22

# Install system dependencies
# python3.11: Runtime for Flask app
# jq: Required by the job-status/retest bash scripts (scripts/*.sh)
# curl is NOT installed: the base image's curl-minimal already provides
# /usr/bin/curl and full curl conflicts with it on UBI 9
RUN dnf install -y \
    python3.11 \
    python3.11-pip \
    jq \
    && dnf clean all

# Install GitHub CLI from the official gh-cli RPM repository
# gh is not available in UBI/RHEL repos (only via ART repos inside CI build pods)
# Required for GitHub API calls (search, retest)
RUN curl -fsSL https://cli.github.com/packages/rpm/gh-cli.repo -o /etc/yum.repos.d/gh-cli.repo && \
    dnf install -y gh && \
    dnf clean all

# Verify gh CLI is available
RUN command -v gh || (echo "ERROR: gh CLI not found after install" && exit 1)

# Install Claude Code CLI via official DNF repository
# Official install docs: https://code.claude.com/docs/en/setup
# Using stable channel for production reliability
RUN curl -fsSL https://downloads.claude.ai/keys/claude-code.asc -o /tmp/claude-code.asc && \
    rpm --import /tmp/claude-code.asc && \
    printf '[claude-code]\nname=Claude Code Stable\nbaseurl=https://downloads.claude.ai/claude-code/rpm/stable\nenabled=1\ngpgcheck=1\ngpgkey=https://downloads.claude.ai/keys/claude-code.asc\n' > /etc/yum.repos.d/claude-code.repo && \
    dnf install -y claude-code && \
    dnf clean all && \
    rm -f /tmp/claude-code.asc

# Verify Claude Code CLI is available
RUN command -v claude || (echo "ERROR: claude CLI not found after install" && exit 1)
RUN claude --version

# Install dashboard as Python package
WORKDIR /app
COPY . /app
RUN pip3.11 install --no-cache-dir /app

# Create runtime user/group for OpenShift compatibility
# OpenShift requires containers to run with GID 0 (root group)
# We set a fixed UID 1001 (not arbitrary UID support)
# chmod g=u ensures root group members have same permissions as the owner
RUN useradd -m -u 1001 -g 0 -s /bin/bash claude && \
    mkdir -p /home/claude/.claude /data && \
    chown -R 1001:0 /home/claude /data && \
    chmod -R g=u /home/claude /data

WORKDIR /home/claude

# Configure environment
ENV HOME=/home/claude
ENV USER=claude

# Switch to runtime user (UID 1001 for vanilla k8s runAsNonRoot validation)
# OpenShift SCC can override this with arbitrary UID from namespace range
# g=u permissions above allow group-writable access for any assigned UID
USER 1001

# Claude Code Vertex AI configuration
# These require runtime secrets mounted:
# - GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp/sa.json (mounted at runtime)
ENV CLAUDE_CODE_USE_VERTEX=1
# <internal> project: human users hold Vertex IAM here (the hybrid project used by
# openshift/release CI only entitles service accounts)
ENV ANTHROPIC_VERTEX_PROJECT_ID=<internal-vertex-project>
ENV CLOUD_ML_REGION=global
# Model must be pinned: newer CLI default models are not enabled on the
# <internal> Vertex projects and 403 (data-sharing / IAM "may not exist" errors)
ENV ANTHROPIC_MODEL=claude-opus-4-6

# Dashboard database path
# Runtime mount: /data PVC for persistence
ENV PR_CI_DASHBOARD_DB=/data/dashboard.db

# Install ai-helpers plugin as runtime user
# Plugin must install after HOME is set and USER is switched
# Plugin install requires network access to plugin registry
# Build will fail in environments without registry access (intentional - no silent partial builds)
# Matches run.sh install flow: marketplace add then plugin install
RUN claude plugin marketplace add openshift-eng/ai-helpers && \
    claude plugin install ci@ai-helpers

# Verify plugin installed successfully via CLI
RUN claude plugin list | grep -q 'ci@ai-helpers' || \
    (echo "ERROR: ci@ai-helpers plugin not found after install" && exit 1)

# Expose Flask default port
EXPOSE 5000

# Run dashboard server using console script entry point
# Uses pr-ci-dashboard CLI installed by pip
# Port defaults to 5000 via DASHBOARD_PORT env var or hardcoded default
ENTRYPOINT ["pr-ci-dashboard"]
