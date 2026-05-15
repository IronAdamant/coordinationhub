#!/usr/bin/env bash
#
# CoordinationHub Release Helper
#
# Usage:
#   ./scripts/release.sh                # Auto-bump patch version
#   ./scripts/release.sh 0.7.13         # Use specific version
#   ./scripts/release.sh --changelog    # Force changelog entry prompt
#
# This script:
#   1. Runs pre-release validation (tests + docs check)
#   2. Bumps version in coordinationhub/__init__.py
#   3. Regenerates documentation
#   4. Optionally creates/updates CHANGELOG.md
#   5. Commits, tags, and pushes (triggering the release workflow)
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

VERSION=""
FORCE_CHANGELOG=false

# Parse arguments
for arg in "$@"; do
  case $arg in
    --changelog)
      FORCE_CHANGELOG=true
      shift
      ;;
    *)
      if [[ -z "$VERSION" ]]; then
        VERSION="$arg"
      fi
      ;;
  esac
done

echo -e "${BLUE}=== CoordinationHub Release Helper ===${NC}"
echo

# --- 1. Pre-flight checks ---
echo -e "${YELLOW}Running pre-release validation...${NC}"

echo "→ Checking documentation is up to date..."
if ! python scripts/gen_docs.py --check >/dev/null 2>&1; then
  echo -e "${RED}ERROR: Documentation is out of date.${NC}"
  echo "Please run: python scripts/gen_docs.py"
  echo "Then commit the changes before releasing."
  exit 1
fi
echo -e "   ${GREEN}✓ Documentation is clean${NC}"

echo "→ Running full test suite..."
if ! python -m pytest tests/ -q --tb=no; then
  echo -e "${RED}ERROR: Tests failed. Fix them before releasing.${NC}"
  exit 1
fi
echo -e "   ${GREEN}✓ All tests passed${NC}"

echo

# --- 2. Determine version ---
if [[ -z "$VERSION" ]]; then
  CURRENT=$(python -c "
import sys
sys.path.insert(0, '.')
import coordinationhub
print(coordinationhub.__version__)
" 2>/dev/null || echo "0.0.0")

  IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"
  NEW_PATCH=$((PATCH + 1))
  VERSION="${MAJOR}.${MINOR}.${NEW_PATCH}"
  echo -e "Auto-bumping version: ${CURRENT} → ${GREEN}${VERSION}${NC}"
else
  echo -e "Using specified version: ${GREEN}${VERSION}${NC}"
fi

# --- 3. Update version ---
echo "__version__ = \"$VERSION\"" > coordinationhub/__init__.py
echo -e "${GREEN}✓ Updated version to $VERSION${NC}"

# --- 4. Regenerate docs ---
echo "→ Regenerating documentation..."
python scripts/gen_docs.py
echo -e "${GREEN}✓ Documentation regenerated${NC}"

# --- 5. Changelog handling ---
CHANGELOG_ENTRY=""

if [[ "$FORCE_CHANGELOG" == true ]] || [[ ! -f CHANGELOG.md ]]; then
  echo
  echo -e "${YELLOW}Would you like to add a changelog entry for v${VERSION}? (y/N)${NC}"
  read -r -n 1 -s REPLY
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Enter a short summary for this release (one line):"
    read -r SUMMARY
    if [[ -n "$SUMMARY" ]]; then
      DATE=$(date +%Y-%m-%d)
      CHANGELOG_ENTRY="## [${VERSION}] - ${DATE}\n\n${SUMMARY}\n"
    fi
  fi
fi

# --- 6. Update or create CHANGELOG.md ---
if [[ -n "$CHANGELOG_ENTRY" ]]; then
  if [[ ! -f CHANGELOG.md ]]; then
    echo -e "# Changelog\n\nAll notable changes to this project will be documented in this file.\n" > CHANGELOG.md
  fi
  
  # Prepend new entry after the header
  TEMP_FILE=$(mktemp)
  {
    head -n 2 CHANGELOG.md
    echo -e "$CHANGELOG_ENTRY"
    tail -n +3 CHANGELOG.md
  } > "$TEMP_FILE"
  mv "$TEMP_FILE" CHANGELOG.md
  
  echo -e "${GREEN}✓ Added entry to CHANGELOG.md${NC}"
  git add CHANGELOG.md
fi

# --- 7. Commit changes ---
echo
echo -e "${YELLOW}Staging changes...${NC}"
git add coordinationhub/__init__.py AGENTS.md COMPLETE_PROJECT_DOCUMENTATION.md LLM_Development.md wiki-local/spec-project.md

if git diff --cached --quiet; then
  echo -e "${YELLOW}No changes to commit.${NC}"
else
  git commit -m "chore: prepare v${VERSION}"
  echo -e "${GREEN}✓ Committed version bump and docs${NC}"
fi

# --- 8. Tag and push ---
echo
echo -e "${YELLOW}Creating tag v${VERSION}...${NC}"
git tag -f "v${VERSION}"

echo -e "${YELLOW}Pushing main and tag v${VERSION}...${NC}"
git push origin main
git push --force origin "v${VERSION}"

echo
echo -e "${GREEN}✅ Release v${VERSION} prepared and pushed successfully!${NC}"
echo
echo "The GitHub Actions workflow 'Release (GitHub + PyPI)' has been triggered."
echo "Monitor progress here:"
echo "  https://github.com/IronAdamant/coordinationhub/actions"
echo
echo "Both the GitHub Release and PyPI publish should complete automatically."
