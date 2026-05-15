#!/usr/bin/env bash
#
# Convenience script to prepare and ship a new CoordinationHub release.
#
# Usage:
#   ./scripts/release.sh          # bumps patch version automatically
#   ./scripts/release.sh 0.7.13   # use a specific version
#
# What it does:
#   1. Bumps the version in coordinationhub/__init__.py
#   2. Runs the test suite
#   3. Regenerates documentation
#   4. Commits the changes
#   5. Creates a git tag
#   6. Pushes main + the tag (which triggers the release workflow)
#
set -euo pipefail

VERSION=${1:-}

if [ -z "$VERSION" ]; then
  # Auto-bump patch version
  CURRENT=$(python -c "import coordinationhub; print(coordinationhub.__version__)")
  IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"
  NEW_PATCH=$((PATCH + 1))
  VERSION="${MAJOR}.${MINOR}.${NEW_PATCH}"
  echo "Auto-bumping version: $CURRENT → $VERSION"
else
  echo "Using provided version: $VERSION"
fi

# 1. Update version
echo "__version__ = \"$VERSION\"" > coordinationhub/__init__.py
echo "Updated coordinationhub/__init__.py to $VERSION"

# 2. Run tests
echo "Running tests..."
python -m pytest tests/ -q

# 3. Regenerate docs
echo "Regenerating documentation..."
python scripts/gen_docs.py

# 4. Commit
echo "Committing changes..."
git add coordinationhub/__init__.py AGENTS.md COMPLETE_PROJECT_DOCUMENTATION.md LLM_Development.md wiki-local/spec-project.md
git commit -m "chore: prepare v$VERSION"

# 5. Tag
echo "Creating tag v$VERSION..."
git tag "v$VERSION"

# 6. Push
echo "Pushing main and tag v$VERSION..."
git push origin main
git push origin "v$VERSION"

echo ""
echo "✅ Release v$VERSION prepared and pushed."
echo "   The GitHub Actions workflow 'Release (GitHub + PyPI)' should now run automatically."
echo "   Monitor it here: https://github.com/IronAdamant/coordinationhub/actions"
