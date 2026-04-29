#!/usr/bin/env bash
# Run the stale-phrase proposer + linter back-to-back. Useful after a
# round of file deletions: the proposer prints candidate STALE_PHRASES
# entries; the linter then catches any leftover stale prose.
#
# Usage: scripts/check_stale_drift.sh
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." &>/dev/null && pwd)"

echo "=== Proposed STALE_PHRASES additions ==="
python "$REPO_ROOT/scripts/propose_stale_phrases.py" "$@"

echo
echo "=== Stale-phrase scan + doc drift check ==="
python "$REPO_ROOT/scripts/gen_docs.py" --check
