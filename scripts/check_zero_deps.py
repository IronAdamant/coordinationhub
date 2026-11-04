#!/usr/bin/env python3
"""Fail when Cargo.toml declares a crate dependency."""

import pathlib
import tomllib

data = tomllib.load(open("Cargo.toml", "rb"))
deps = data.get("dependencies") or {}
dev = data.get("dev-dependencies") or {}
build = data.get("build-dependencies") or {}
if deps or dev or build:
    raise SystemExit(f"dependency tables must be empty: {deps} {dev} {build}")
if pathlib.Path("build.rs").exists():
    raise SystemExit("build.rs is not allowed")
print("dependency_count 0")
