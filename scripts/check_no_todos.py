#!/usr/bin/env python3
"""Fail when source contains unfinished markers."""

import pathlib
import re

pattern = re.compile(r"\b(TODO|FIXME|unimplemented!|todo!)\b|placeholder")
roots = [pathlib.Path("src"), pathlib.Path("tests")]
failed = []
for root in roots:
    for path in root.rglob("*"):
        if path.suffix not in {".rs", ".py", ".md"} and path.suffix:
            continue
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if pattern.search(text):
            failed.append(str(path))
if failed:
    raise SystemExit("markers found: " + ", ".join(failed))
print("no markers")
