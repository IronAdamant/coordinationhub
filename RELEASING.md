# Releasing CoordinationHub

A release is a `vX.Y.Z` tag on `main`. Pushing the tag runs `.github/workflows/release.yml`, which builds the binaries and publishes the GitHub Release. Nothing is published to a package registry, and no token or secret is stored in the repository: the workflow uses the run's own `GITHUB_TOKEN`.

## 1. Bump the version in a pull request

The version lives in these places; all of them must say X.Y.Z:

| Where | What |
| --- | --- |
| `Cargo.toml` | `version = "X.Y.Z"` under `[package]` |
| `Cargo.lock` | the `coordinationhub` package's `version`; run `cargo build` after editing `Cargo.toml` and commit the result |
| `assets/tools.json` | `"tools_version": "X.Y.Z"` (the HTTP `/health` and `/tools` replies show it) |
| `src/validate.rs` | the `tools_version` fallback in `load_schema` |
| `tests/argparse_goldens.rs` | the four `coordinationhub X.Y.Z` lines of the `--version` cases |
| `CHANGELOG.md` | a `## [X.Y.Z] - YYYY-MM-DD` section |

`--version` itself prints `env!("CARGO_PKG_VERSION")` (`src/lib.rs`), so it follows `Cargo.toml`. Update `README.md`, `MIGRATION.md` and `LLM_Development.md` in the same pull request when the release changes behaviour.

Merge the pull request only when every CI job is green: tests on six platforms, clippy, `check_zero_deps`, `check_no_todos`, the parity harness against 0.8.0, and `benchmark-gate`.

## 2. Check the tree

On an up-to-date `main`:

```bash
git checkout main && git pull
scripts/release.sh X.Y.Z
```

The script checks that the tag is new, that `main` is clean and equal to `origin/main`, that `Cargo.toml`, `Cargo.lock`, `assets/tools.json`, `src/validate.rs` and `CHANGELOG.md` all say X.Y.Z, and runs `cargo test`, clippy, `check_zero_deps` and `check_no_todos` (skip those with `--skip-checks` when CI has just run them on the same commit). It prints the tag command. It does not commit, tag, push or publish anything.

## 3. Tag and push

```bash
git tag -a vX.Y.Z -m "CoordinationHub X.Y.Z" && git push origin vX.Y.Z
```

The Release workflow then:

1. fails unless the tag equals `v` plus the `Cargo.toml` version;
2. builds release binaries for Linux x64 and arm64, macOS x64 and arm64, and Windows x64 on Rust 1.83.0, each packed as a `.tar.gz`;
3. writes `SHA256SUMS` and checks it;
4. creates the GitHub Release for the tag with the archives and `SHA256SUMS`, or replaces the assets of one that already exists (a rerun).

Do not push a tag from a pull request branch. To redo a failed run, rerun the workflow from the Actions tab; moving a published tag is a last resort.
