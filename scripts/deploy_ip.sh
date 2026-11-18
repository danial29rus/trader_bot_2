#!/usr/bin/env bash
# Copy this working tree to a Linux server over SSH and rebuild the stack.
# Existing remote .env and database volumes are preserved.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: bash scripts/deploy_ip.sh user@SERVER_IP [remote-directory]" >&2
  exit 2
fi

TARGET=$1
REMOTE_DIR=${2:-/opt/honestplay}
ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)

command -v ssh >/dev/null || { echo "ssh is required" >&2; exit 1; }
command -v rsync >/dev/null || { echo "rsync is required (install with: brew install rsync)" >&2; exit 1; }

echo "Checking SSH access to $TARGET"
ssh -o BatchMode=yes "$TARGET" "mkdir -p '$REMOTE_DIR'"

echo "Copying project files; local .env and .git are excluded"
rsync -az \
  --exclude='.git/' \
  --exclude='.env' \
  --exclude='__pycache__/' \
  --exclude='.pytest_cache/' \
  --exclude='admin_web/node_modules/' \
  "$ROOT_DIR/" "$TARGET:$REMOTE_DIR/"

HAS_ENV=$(ssh "$TARGET" "test -f '$REMOTE_DIR/.env' && echo yes || echo no")
if [[ "$HAS_ENV" == "yes" ]]; then
  echo "Existing server .env preserved; rebuilding services"
  ssh -t "$TARGET" "cd '$REMOTE_DIR' && docker compose --profile public up -d --build"
else
  echo "No server .env found; starting interactive first-time setup"
  ssh -t "$TARGET" "cd '$REMOTE_DIR' && bash scripts/setup.sh"
fi

echo "Deployment finished. Check: ssh $TARGET 'cd $REMOTE_DIR && docker compose ps'"
