#!/bin/bash
# =============================================================================
# Deploy Script
# =============================================================================
# Deploys agent-server to a remote host via SSH.
#
# Usage:
#   ./deploy.sh                      # uses DEPLOY_TARGET env var
#   ./deploy.sh salinas              # deploy to 'salinas'
#   ./deploy.sh user@host.example    # deploy to specific user@host
#
# Environment variables:
#   DEPLOY_TARGET   - Default SSH target (e.g., "salinas" or "user@host")
#   DEPLOY_PATH     - Remote path (default: ~/agent-server)
#   GITHUB_REPO     - Repository URL (default: from local git remote)

set -e

# =============================================================================
# Configuration
# =============================================================================

# Target: CLI arg > env var > error
TARGET="${1:-${DEPLOY_TARGET:-}}"
if [ -z "$TARGET" ]; then
    echo "Error: No deploy target specified."
    echo "Usage: ./deploy.sh <target>"
    echo "   or: DEPLOY_TARGET=<target> ./deploy.sh"
    exit 1
fi

# Remote path
REMOTE_PATH="${DEPLOY_PATH:-~/agent-server}"

# GitHub repo: env var > detect from local git
if [ -z "$GITHUB_REPO" ]; then
    GITHUB_REPO=$(git remote get-url origin 2>/dev/null || echo "")
fi

if [ -z "$GITHUB_REPO" ]; then
    echo "Error: Could not detect GitHub repo. Set GITHUB_REPO env var."
    exit 1
fi

# =============================================================================
# Deploy
# =============================================================================

echo "=== Deploying to ${TARGET} ==="
echo "Repository: ${GITHUB_REPO}"
echo "Remote path: ${REMOTE_PATH}"
echo

# Build the remote commands
read -r -d '' REMOTE_SCRIPT << 'EOF' || true
set -e

REMOTE_PATH="__REMOTE_PATH__"
GITHUB_REPO="__GITHUB_REPO__"

# Clone if not present
if [ ! -d "$REMOTE_PATH" ]; then
    echo ">>> Cloning repository..."
    git clone "$GITHUB_REPO" "$REMOTE_PATH"
    cd "$REMOTE_PATH"
else
    echo ">>> Pulling latest changes..."
    cd "$REMOTE_PATH"
    git fetch origin
    git reset --hard origin/main  # or origin/master if that's your branch
fi

# Show current commit
echo ">>> Current commit:"
git log -1 --oneline

# Pull latest container images
echo ">>> Pulling container images..."
podman-compose pull || docker compose pull

# Build and start
echo ">>> Building and starting containers..."
podman-compose up -d --build || docker compose up -d --build

# Show status
echo ">>> Container status:"
podman ps || docker ps

echo
echo "=== Deploy complete ==="
EOF

# Substitute variables into the script
REMOTE_SCRIPT="${REMOTE_SCRIPT//__REMOTE_PATH__/$REMOTE_PATH}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__GITHUB_REPO__/$GITHUB_REPO}"

# Execute on remote
echo ">>> Connecting to ${TARGET}..."
ssh "$TARGET" "$REMOTE_SCRIPT"

echo
echo "Done! Deployed to ${TARGET}:${REMOTE_PATH}"
