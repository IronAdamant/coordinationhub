#!/bin/sh
# Install CoordinationHub's git hooks into .git/hooks. The pre-commit hook
# runs check_zero_deps, check_no_todos and the AGENTS.md path test.
set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS="$(git -C "$REPO_ROOT" rev-parse --git-path hooks)"
case "$HOOKS" in
  /*) ;;
  *) HOOKS="$REPO_ROOT/$HOOKS" ;;
esac

mkdir -p "$HOOKS"
cp "$REPO_ROOT/scripts/hooks/pre-commit" "$HOOKS/pre-commit"
chmod +x "$HOOKS/pre-commit"
echo "pre-commit hook installed in $HOOKS"
