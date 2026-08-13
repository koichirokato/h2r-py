#!/usr/bin/env bash
# Pre-push checks: run automatically before every git push via Claude Code hook.
# All checks must pass for the push to proceed.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

make check

echo "All pre-push checks passed."
