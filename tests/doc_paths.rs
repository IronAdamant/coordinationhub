//! Every repository path the agent guides name must exist in the tree.
//!
//! `AGENTS.md`, `CLAUDE.md` and `COMPLETE_PROJECT_DOCUMENTATION.md` are read
//! by agents working on this repo; a path in them that is gone sends an
//! agent looking for a file that is not there (until 1.3.0 `AGENTS.md`
//! described a Python package and an SQLite database).
//!
//! Checked: inline code spans and Markdown link targets outside fenced
//! blocks. A span is a path when it holds only letters, digits and `_ . / * -`,
//! does not start with `/` or `-`, and contains a `/` or ends in a file
//! extension from `EXTENSIONS`. `*` matches within one path component and
//! must match at least one entry; a trailing `/` must be a directory.
//! `NOT_PATHS` lists the spans that look like paths but name something
//! else: files the program writes at run time and MCP method names.

use std::path::Path;

const DOCS: &[&str] = &["AGENTS.md", "CLAUDE.md", "COMPLETE_PROJECT_DOCUMENTATION.md"];

const EXTENSIONS: &[&str] = &[
    "rs", "md", "toml", "lock", "json", "yaml", "yml", "sh", "html", "js", "txt", "png",
];

/// Spans that are not repository paths.
const NOT_PATHS: &[&str] = &[
    // Written by the program into a store or the home directory.
    ".coordinationhub/",
    "coordination.jsonl",
    "coordination.lock",
    "hooks.json",
    "hooks.json.bak",
    "dashboard.log",
    // MCP methods.
    "tools/list",
    "tools/call",
    "notifications/tools/list_changed",
];

/// Inline code spans and link targets outside fenced code blocks.
fn spans(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut fenced = false;
    for line in text.lines() {
        if line.trim_start().starts_with("```") {
            fenced = !fenced;
            continue;
        }
        if fenced {
            continue;
        }
        let mut parts = line.split('`');
        parts.next();
        while let Some(code) = parts.next() {
            out.push(code.to_string());
            if parts.next().is_none() {
                break;
            }
        }
        let mut rest = line;
        while let Some(at) = rest.find("](") {
            let target = &rest[at + 2..];
            let end = target.find(')').unwrap_or(target.len());
            let link = target[..end].split('#').next().unwrap_or("");
            if !link.is_empty() && !link.contains("://") && !link.starts_with("mailto:") {
                out.push(link.to_string());
            }
            rest = &target[end..];
        }
    }
    out
}

fn is_path(span: &str) -> bool {
    if span.is_empty()
        || span.starts_with('/')
        || span.starts_with('-')
        || NOT_PATHS.contains(&span)
        || !span.chars().all(|c| c.is_ascii_alphanumeric() || "_./*-".contains(c))
    {
        return false;
    }
    let extension = span.rsplit('/').next().and_then(|name| name.rsplit_once('.')).map(|(_, ext)| ext);
    span.contains('/') || extension.is_some_and(|ext| EXTENSIONS.contains(&ext))
}

/// `*` in one component matches any run of characters within it.
fn glob_match(pattern: &str, name: &str) -> bool {
    match pattern.split_once('*') {
        None => pattern == name,
        Some((head, tail)) => {
            name.len() >= head.len() + tail.len()
                && name.starts_with(head)
                && (0..=name.len() - head.len()).any(|at| {
                    name.is_char_boundary(head.len() + at) && glob_match(tail, &name[head.len() + at..])
                })
        }
    }
}

/// Whether `path` (relative, `/`-separated, maybe with `*`) names
/// something under `root`.
fn exists(root: &Path, path: &str) -> bool {
    let want_dir = path.ends_with('/');
    let parts: Vec<&str> = path.trim_end_matches('/').split('/').collect();
    let mut found = vec![root.to_path_buf()];
    for (index, part) in parts.iter().enumerate() {
        let last = index + 1 == parts.len();
        let mut next = Vec::new();
        for dir in &found {
            if part.contains('*') {
                let Ok(entries) = std::fs::read_dir(dir) else { continue };
                for entry in entries.flatten() {
                    if glob_match(part, &entry.file_name().to_string_lossy()) {
                        next.push(entry.path());
                    }
                }
            } else {
                let candidate = dir.join(part);
                if candidate.exists() {
                    next.push(candidate);
                }
            }
        }
        if last && want_dir {
            next.retain(|path| path.is_dir());
        }
        found = next;
    }
    !found.is_empty()
}

/// The path spans in `text` that do not exist under `root`.
fn missing(root: &Path, text: &str) -> Vec<String> {
    let mut out: Vec<String> = spans(text)
        .into_iter()
        .filter(|span| is_path(span) && !exists(root, span))
        .collect();
    out.dedup();
    out
}

#[test]
fn the_agent_guides_name_only_paths_in_the_tree() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    for doc in DOCS {
        let text = std::fs::read_to_string(root.join(doc)).unwrap();
        let gone = missing(root, &text);
        assert!(gone.is_empty(), "{doc} names paths that are not in the tree: {gone:?}; fix the path or remove it");
    }
}

#[test]
fn agents_md_stays_short() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let size = std::fs::metadata(root.join("AGENTS.md")).unwrap().len();
    assert!(size < 12 * 1024, "AGENTS.md is {size} bytes; keep it under 12 KB and point to the longer documents");
}

#[test]
fn a_missing_path_is_reported_and_the_rest_is_not() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let text = "\
Module `src/engine.rs`, the `src/act_*.rs` files, the `examples/` folder, `Cargo.toml`.
Gone: `coordinationhub/db.py`, `src/nope_*.rs`, `no_such_guide.md`, `Cargo.toml/` and [old](docs/gone/index.md#top).
Not paths: `cargo test --release`, `json::parse`, `tools/list`, `coordination.jsonl`, `/call`, `--json`,
`1.83.0`, `<python tree>`, [site](https://example.com/a.md).
```text
`fenced/not/checked.rs`
```
";
    assert_eq!(
        missing(root, text),
        ["coordinationhub/db.py", "src/nope_*.rs", "no_such_guide.md", "Cargo.toml/", "docs/gone/index.md"]
    );
}

#[test]
fn glob_matches_within_one_component() {
    assert!(glob_match("act_*.rs", "act_lock.rs"));
    assert!(glob_match("*.rs", "a.rs"));
    assert!(!glob_match("act_*.rs", "act_lock.md"));
    assert!(!glob_match("act_*.rs", "xact_lock.rs"));
}
