//! Project-root detection and path normalisation matching the Python helpers.

use std::path::{Component, Path, PathBuf};

pub fn detect_project_root(start: &Path) -> Option<PathBuf> {
    let mut path = start.to_path_buf();
    for _ in 0..256 {
        let git = path.join(".git");
        if git.is_dir() || git.is_file() {
            return Some(path);
        }
        if !path.pop() {
            break;
        }
    }
    None
}

/// Logical resolve: absolute, `..` collapsed, no symlink requirement.
pub fn logical_resolve(path: &str, base: &Path) -> PathBuf {
    let raw = Path::new(path);
    let joined = if raw.is_absolute() {
        raw.to_path_buf()
    } else {
        base.join(raw)
    };
    let mut out = PathBuf::new();
    for c in joined.components() {
        match c {
            Component::CurDir => {}
            Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

pub fn normalize_path(path: &str, project_root: &Path) -> String {
    let resolved = logical_resolve(path, project_root);
    let root = logical_resolve(project_root.to_string_lossy().as_ref(), project_root);
    if let Ok(rel) = resolved.strip_prefix(&root) {
        let s = rel.to_string_lossy().replace('\\', "/");
        if s.is_empty() {
            return ".".into();
        }
        return s;
    }
    resolved.to_string_lossy().replace('\\', "/")
}

pub fn posix(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}
