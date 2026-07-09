#!/usr/bin/env bash
# scripts/setup-worktree.sh
#
# Set up frontend assets in a fresh haute worktree. Idempotent — re-running
# is a no-op once frontend/node_modules and src/haute/static/ exist.
#
# Run from anywhere; it resolves the repo root from its own location.
# `haute serve` points here when it detects a source checkout with no
# built frontend.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -d frontend/node_modules && -f src/haute/static/index.html ]]; then
  echo "[setup-worktree] frontend already set up at $REPO_ROOT/frontend; nothing to do"
  exit 0
fi

echo "[setup-worktree] worktree: $REPO_ROOT"

if [[ ! -d frontend/node_modules ]]; then
  echo "[setup-worktree] installing npm deps..."
  (cd frontend && npm install)
fi

if [[ ! -f src/haute/static/index.html ]]; then
  echo "[setup-worktree] building frontend..."
  (cd frontend && npm run build)
fi

echo "[setup-worktree] done. 'uv run haute serve' will work now."
