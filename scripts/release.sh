#!/usr/bin/env bash
# Release check for CoordinationHub. Run it on main after the version bump
# is merged. It checks the tree and prints the tag command; it does not
# commit, tag, push or publish anything.
#
# Usage: scripts/release.sh X.Y.Z [--skip-checks]
#
# Checks, in order:
#   1. X.Y.Z is a version, and the tag vX.Y.Z does not exist yet.
#   2. The checkout is on main, has no changes, and matches origin/main.
#   3. Every place that keeps the version says X.Y.Z: Cargo.toml,
#      Cargo.lock, assets/tools.json (tools_version), the fallback in
#      src/validate.rs, and a "## [X.Y.Z]" heading in CHANGELOG.md.
#   4. Unless --skip-checks: cargo test, clippy with -D warnings,
#      check_zero_deps and check_no_todos pass.
# parity and bench need Python 0.8.0 and run in CI (parity harness,
# benchmark-gate); the merged pull request must be green there.
#
# Pushing the tag runs .github/workflows/release.yml, which refuses a tag
# that differs from Cargo.toml, builds five binaries, writes SHA256SUMS and
# publishes the GitHub Release.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fail() {
  echo "error: $*" >&2
  exit 1
}

V="${1:-}"
SKIP_CHECKS=false
if [ "${2:-}" = "--skip-checks" ]; then
  SKIP_CHECKS=true
elif [ -n "${2:-}" ]; then
  fail "unknown option ${2}; usage: scripts/release.sh X.Y.Z [--skip-checks]"
fi
if ! printf '%s\n' "$V" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  fail "give the version as X.Y.Z (got \"$V\"); usage: scripts/release.sh X.Y.Z [--skip-checks]"
fi

# 1. The tag must be new.
if git rev-parse -q --verify "refs/tags/v$V" >/dev/null; then
  fail "tag v$V already exists locally; pick the next version"
fi
git fetch --quiet origin main --tags
if git ls-remote --exit-code --tags origin "refs/tags/v$V" >/dev/null 2>&1; then
  fail "tag v$V already exists on origin; pick the next version"
fi

# 2. A clean main that matches origin/main.
branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "main" ] || fail "on branch $branch; run git checkout main && git pull first"
[ -z "$(git status --porcelain)" ] || fail "the tree has changes; commit or stash them first (git status)"
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
  || fail "main is not origin/main; run git pull (or push the merge) first"
echo "OK: clean main at $(git rev-parse --short HEAD), tag v$V is free"

# 3. Every version agrees.
cargo_version="$(grep -m1 '^version = ' Cargo.toml | cut -d'"' -f2)"
lock_version="$(grep -A1 '^name = "coordinationhub"$' Cargo.lock | grep -m1 '^version = ' | cut -d'"' -f2)"
tools_version="$(grep -m1 '"tools_version"' assets/tools.json | cut -d'"' -f4)"
fallback_version="$(grep -m1 'unwrap_or("[0-9]' src/validate.rs | cut -d'"' -f2)"
bad=""
[ "$cargo_version" = "$V" ] || bad="$bad\n  Cargo.toml version is $cargo_version"
[ "$lock_version" = "$V" ] || bad="$bad\n  Cargo.lock coordinationhub version is $lock_version (run cargo build after editing Cargo.toml)"
[ "$tools_version" = "$V" ] || bad="$bad\n  assets/tools.json tools_version is $tools_version"
[ "$fallback_version" = "$V" ] || bad="$bad\n  src/validate.rs tools_version fallback is $fallback_version"
grep -q "^## \[$V\]" CHANGELOG.md || bad="$bad\n  CHANGELOG.md has no \"## [$V]\" heading"
if [ -n "$bad" ]; then
  printf "error: the version is not %s everywhere:%b\nFix these in a pull request, merge it, then rerun.\n" "$V" "$bad" >&2
  exit 1
fi
echo "OK: Cargo.toml, Cargo.lock, assets/tools.json, src/validate.rs and CHANGELOG.md say $V"

# 4. The checks CI runs that need no Python.
if [ "$SKIP_CHECKS" = true ]; then
  echo "SKIPPED: tests and checks (--skip-checks)"
else
  cargo test --locked --release
  cargo clippy --locked --all-targets -- -D warnings
  cargo run --quiet --locked --release --example check_zero_deps
  cargo run --quiet --locked --release --example check_no_todos
  echo "OK: tests, clippy, check_zero_deps and check_no_todos pass"
fi

echo
echo "Next, when CI on this commit is green, tag it and push the tag:"
echo "  git tag -a v$V -m \"CoordinationHub $V\" && git push origin v$V"
echo "The tag runs Release: five binaries, SHA256SUMS and the GitHub Release."
echo "This script did not tag or push anything."
