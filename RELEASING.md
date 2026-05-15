# Releasing CoordinationHub

This project uses a fully automated, secret-free release pipeline based on Git tags + GitHub OIDC Trusted Publishing.

## One-time setup (required)

### 1. GitHub Environment + Trusted Publisher

1. Go to https://github.com/IronAdamant/coordinationhub/settings/environments
2. Create a new environment named exactly **`pypi`**.
3. (Optional but recommended) Add any required reviewers or wait timers for manual oversight.

### 2. Register Trusted Publisher on PyPI

1. Go to https://pypi.org/manage/project/coordinationhub/settings/publishing/
2. Click **"Add a trusted publisher"**.
3. Choose **GitHub**.
4. Fill in:
   - **Repository**: `IronAdamant/coordinationhub`
   - **Workflow name**: `publish.yml` (this is the one that actually uploads)
   - **Environment name**: `pypi`
5. Save.

This allows the GitHub Actions OIDC token to be exchanged for a short-lived PyPI token **with no API token ever stored in the repo**.

## Normal release process (fully automatic)

```bash
# 1. Bump the version (single source of truth)
vim coordinationhub/__init__.py   # change __version__ = "0.7.10"

# 2. Run tests + regenerate docs locally (recommended)
python -m pytest tests/ -q
python scripts/gen_docs.py

# 3. Commit + push the version bump
git add coordinationhub/__init__.py AGENTS.md COMPLETE_PROJECT_DOCUMENTATION.md ...
git commit -m "chore: prepare v0.7.10"
git push origin main

# 4. Tag and push the tag (this is the trigger)
git tag v0.7.10
git push origin v0.7.10
```

What happens automatically:

1. The `release.yml` workflow triggers on the `v*` tag.
2. It builds the sdist + wheel.
3. It creates a proper **GitHub Release** (visible on the Releases tab) and attaches the `.tar.gz` + `.whl` as downloadable assets.
4. Creating the GitHub Release fires the `release: published` event.
5. The `publish.yml` workflow runs, uses GitHub OIDC to mint a short-lived PyPI token, and uploads to PyPI.
6. The new version appears on https://pypi.org/project/coordinationhub/ within a few minutes.

No `twine` commands, no API tokens, no manual steps after the tag push.

## Emergency / manual release

If the automation fails, you can still:

```bash
python -m build
twine upload dist/*          # requires your PyPI token in env or keyring
```

But the goal is to never need this.

## Versioning policy

- Version lives **only** in `coordinationhub/__init__.py` (`__version__`).
- `pyproject.toml` uses `dynamic = ["version"]` + `tool.setuptools.dynamic.version = {attr = "coordinationhub.__version__"}`.
- We follow semantic versioning (MAJOR.MINOR.PATCH). Most releases are PATCH.

## Workflow files

- `.github/workflows/release.yml` — Creates the GitHub Release + attaches artifacts (triggered by tag)
- `.github/workflows/publish.yml` — Publishes to PyPI via OIDC (triggered by GitHub Release creation)
- `.github/workflows/test.yml` — Runs the full test matrix on every push/PR

Both release-related workflows deliberately avoid third-party GitHub Actions (matching the project's zero-dependency philosophy).
