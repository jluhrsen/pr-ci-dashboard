#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "🚀 PR CI Dashboard - Quick Start"
echo ""

# Check gh CLI authentication
echo "Checking GitHub CLI authentication..."
if ! command -v gh &> /dev/null; then
    echo -e "${RED}❌ GitHub CLI (gh) not found${NC}"
    echo "Install it from: https://cli.github.com"
    exit 1
fi

if ! gh auth status &> /dev/null; then
    echo -e "${RED}❌ GitHub CLI not authenticated${NC}"
    echo "Run: gh auth login"
    exit 1
fi
echo -e "${GREEN}✅ GitHub CLI authenticated${NC}"
echo ""

# Check Python
echo "Checking Python..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 not found${NC}"
    exit 1
fi
PYTHON_VERSION=$(python3 --version | awk '{print $2}')
echo -e "${GREEN}✅ Python ${PYTHON_VERSION} found${NC}"
echo ""

# Setup temp directory
TMP_DIR="/tmp/pr-ci-dashboard-$USER"
echo "Setting up in ${TMP_DIR}..."

# Clean up old instance if exists
if [ -d "$TMP_DIR" ]; then
    echo "Removing old instance..."
    rm -rf "$TMP_DIR"
fi

mkdir -p "$TMP_DIR"
cd "$TMP_DIR"

# Download pr-ci-dashboard folder from GitHub
echo "Downloading pr-ci-dashboard..."
curl -fsSL https://github.com/jluhrsen/workBits/archive/refs/heads/main.tar.gz | \
    tar xz --strip-components=1 workBits-main/pr-ci-dashboard
cd pr-ci-dashboard

# Create virtual environment
echo "Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo ""
echo -e "${GREEN}✅ Setup complete!${NC}"
echo ""
echo "Starting dashboard at http://localhost:5000"
echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
echo ""

# Cleanup function
cleanup() {
    echo ""
    echo "Shutting down..."
    echo "Cleaning up ${TMP_DIR}..."
    rm -rf "$TMP_DIR"
    echo "Done!"
    exit 0
}

trap cleanup INT TERM

# Run the server
python server.py "$@"
