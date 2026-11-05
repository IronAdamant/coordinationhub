//! Fail when source contains unfinished markers.
//!
//! Usage: cargo run --release --example check_no_todos [-- ROOT]
//!
//! Walks `src` and `tests` under ROOT (default: the current directory) in
//! the order Python's `Path.rglob("*")` yields them, reads every `.rs`,
//! `.py`, `.md` or suffix-less file, and matches the same pattern the
//! Python script used: `\b(TODO|FIXME|unimplemented!|todo!)\b|placeholder`.
//! Prints `no markers`, or `markers found: a, b` on stderr with exit 1.

use std::path::{Path, PathBuf};

fn word_char(ch: Option<char>) -> bool {
    ch.is_some_and(|ch| ch.is_alphanumeric() || ch == '_')
}

/// `\b(TODO|FIXME|unimplemented!|todo!)\b|placeholder` anywhere in `text`.
fn has_marker(text: &str) -> bool {
    if text.contains("placeholder") {
        return true;
    }
    for word in ["TODO", "FIXME", "unimplemented!", "todo!"] {
        let mut from = 0;
        while let Some(found) = text[from..].find(word) {
            let start = from + found;
            let end = start + word.len();
            let before = text[..start].chars().next_back();
            let first = word.chars().next();
            let last = word.chars().next_back();
            let after = text[end..].chars().next();
            // \b before the word: boundary between `before` and its first char.
            let open = word_char(before) != word_char(first);
            // \b after the word: boundary between its last char and `after`.
            let close = word_char(last) != word_char(after);
            if open && close {
                return true;
            }
            from = start + word.chars().next().map(char::len_utf8).unwrap_or(1);
        }
    }
    false
}

/// `pathlib.Path.rglob("*")` order on CPython 3.12: a directory's entries
/// in `os.scandir` order, then each subdirectory the same way, depth first.
fn rglob(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let entries: Vec<PathBuf> = entries.filter_map(|entry| entry.ok().map(|e| e.path())).collect();
    out.extend(entries.iter().cloned());
    for entry in entries {
        let is_dir = std::fs::symlink_metadata(&entry).map(|m| m.is_dir()).unwrap_or(false);
        if is_dir {
            rglob(&entry, out);
        }
    }
}

fn main() {
    let root = std::env::args().nth(1).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
    let mut failed: Vec<String> = Vec::new();
    for top in ["src", "tests"] {
        let mut paths = Vec::new();
        rglob(&root.join(top), &mut paths);
        for path in paths {
            let suffix = path
                .file_name()
                .and_then(|name| name.to_str())
                .and_then(|name| name.rfind('.').filter(|at| *at > 0 && *at + 1 < name.len()).map(|at| name[at..].to_string()))
                .unwrap_or_default();
            if !suffix.is_empty() && ![".rs", ".py", ".md"].contains(&suffix.as_str()) {
                continue;
            }
            if !path.is_file() {
                continue;
            }
            let bytes = std::fs::read(&path).unwrap_or_default();
            if has_marker(&String::from_utf8_lossy(&bytes)) {
                let shown = path.strip_prefix(&root).unwrap_or(&path);
                failed.push(shown.display().to_string());
            }
        }
    }
    if !failed.is_empty() {
        eprintln!("markers found: {}", failed.join(", "));
        std::process::exit(1);
    }
    println!("no markers");
}
