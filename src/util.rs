//! Clock, paths, limits, and a process file lock. No crates.

use std::fs::File;
use std::io::Error;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub fn local_stamp(epoch: f64) -> String {
    let secs = epoch as i64;
    #[cfg(unix)]
    {
        #[repr(C)]
        struct Tm {
            tm_sec: i32,
            tm_min: i32,
            tm_hour: i32,
            tm_mday: i32,
            tm_mon: i32,
            tm_year: i32,
            tm_wday: i32,
            tm_yday: i32,
            tm_isdst: i32,
            tm_gmtoff: i64,
            tm_zone: *const i8,
        }
        unsafe extern "C" {
            fn localtime_r(timep: *const i64, result: *mut Tm) -> *mut Tm;
        }
        let mut tm = unsafe { std::mem::zeroed::<Tm>() };
        unsafe {
            localtime_r(&secs, &mut tm);
        }
        return format!(
            "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
            tm.tm_year + 1900,
            tm.tm_mon + 1,
            tm.tm_mday,
            tm.tm_hour,
            tm.tm_min,
            tm.tm_sec
        );
    }
    // Windows: kernel32 converts with the active time zone's rules for
    // that date, which is what the C runtime's localtime does. kernel32 is
    // linked on every Windows target; naming a C runtime here would mix
    // runtimes on the GNU target.
    #[cfg(windows)]
    {
        #[repr(C)]
        struct FileTime {
            low: u32,
            high: u32,
        }
        #[repr(C)]
        #[derive(Default)]
        struct SystemTimeParts {
            year: u16,
            month: u16,
            day_of_week: u16,
            day: u16,
            hour: u16,
            minute: u16,
            second: u16,
            milliseconds: u16,
        }
        unsafe extern "system" {
            fn FileTimeToSystemTime(file_time: *const FileTime, system_time: *mut SystemTimeParts) -> i32;
            fn SystemTimeToTzSpecificLocalTime(
                zone: *const std::ffi::c_void,
                universal: *const SystemTimeParts,
                local: *mut SystemTimeParts,
            ) -> i32;
        }
        const EPOCH_DIFFERENCE_SECS: i64 = 11_644_473_600;
        let ticks = ((secs + EPOCH_DIFFERENCE_SECS).max(0) as u64) * 10_000_000;
        let file_time = FileTime {
            low: ticks as u32,
            high: (ticks >> 32) as u32,
        };
        let mut universal = SystemTimeParts::default();
        let mut local = SystemTimeParts::default();
        unsafe {
            FileTimeToSystemTime(&file_time, &mut universal);
            if SystemTimeToTzSpecificLocalTime(std::ptr::null(), &universal, &mut local) == 0 {
                local = universal;
            }
        }
        let _ = (local.day_of_week, local.milliseconds);
        format!(
            "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
            local.year, local.month, local.day, local.hour, local.minute, local.second
        )
    }
}

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

pub fn pid() -> i64 {
    std::process::id() as i64
}

pub fn env_i64(name: &str, default: i64) -> i64 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

pub fn env_f64(name: &str, default: f64) -> f64 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

pub fn truncate(text: Option<String>, max_chars: i64) -> Option<String> {
    let Some(text) = text else {
        return None;
    };
    let max_chars = max_chars.max(0) as usize;
    let count = text.chars().count();
    if count <= max_chars {
        return Some(text);
    }
    Some(text.chars().take(max_chars).collect())
}

pub fn detect_project_root(start: &Path) -> Option<PathBuf> {
    let mut path = if start.is_absolute() {
        start.to_path_buf()
    } else {
        std::env::current_dir().ok()?.join(start)
    };
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

pub fn normalize_path(path: &str, project_root: Option<&Path>) -> String {
    let resolved = soft_resolve(Path::new(path));
    if let Some(root) = project_root {
        let root = soft_resolve(root);
        if let Ok(rel) = resolved.strip_prefix(&root) {
            let text = rel.to_string_lossy().replace('\\', "/");
            if text.is_empty() {
                return ".".into();
            }
            return text;
        }
    }
    resolved.to_string_lossy().replace('\\', "/")
}

/// `pathlib.Path(path).resolve()` (non-strict `os.path.realpath`).
pub fn soft_resolve(path: &Path) -> PathBuf {
    realpath(path)
}

/// `str(Path(path).resolve())`: native separators, as Python prints it.
pub fn path_str(path: &Path) -> String {
    realpath(path).to_string_lossy().into_owned()
}

/// CPython's `ntpath.realpath(path, strict=False)`: normalise, make
/// absolute, resolve the longest existing ancestor through the file system,
/// join the rest, and drop the `\\?\` prefix Windows hands back.
#[cfg(windows)]
fn realpath(path: &Path) -> PathBuf {
    use std::path::Component;
    const VERBATIM: &str = r"\\?\";
    let had_prefix = path.as_os_str().to_string_lossy().starts_with(VERBATIM);
    let joined = if had_prefix || path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(path)
    };
    let mut head = PathBuf::new();
    for component in joined.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                if !matches!(head.components().next_back(), Some(Component::RootDir) | Some(Component::Prefix(_)) | None) {
                    head.pop();
                }
            }
            other => head.push(other.as_os_str()),
        }
    }
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    let resolved = loop {
        if let Ok(found) = std::fs::canonicalize(&head) {
            break found;
        }
        match (head.parent(), head.file_name()) {
            (Some(parent), Some(name)) => {
                tail.push(name.to_os_string());
                head = parent.to_path_buf();
            }
            _ => break head,
        }
    };
    let mut out = resolved;
    for name in tail.iter().rev() {
        out.push(name);
    }
    if had_prefix {
        return out;
    }
    let text = out.to_string_lossy().into_owned();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    if let Some(rest) = text.strip_prefix(VERBATIM) {
        let bytes = rest.as_bytes();
        if bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':' {
            return PathBuf::from(rest);
        }
    }
    out
}

/// CPython's `posixpath.realpath(path, strict=False)`: walk the components,
/// follow symbolic links, keep missing components as written, then
/// `abspath`.
#[cfg(not(windows))]
fn realpath(path: &Path) -> PathBuf {
    fn join(base: &str, name: &str) -> String {
        if name.starts_with('/') {
            name.to_string()
        } else if base.is_empty() || base.ends_with('/') {
            format!("{base}{name}")
        } else {
            format!("{base}/{name}")
        }
    }
    fn split(path: &str) -> (String, String) {
        let cut = path.rfind('/').map(|i| i + 1).unwrap_or(0);
        let (head, tail) = path.split_at(cut);
        let trimmed = head.trim_end_matches('/');
        let head = if !head.is_empty() && trimmed.is_empty() { head.to_string() } else { trimmed.to_string() };
        (head, tail.to_string())
    }
    fn walk(
        mut path: String,
        rest: &str,
        seen: &mut Vec<(String, Option<String>)>,
    ) -> (String, bool) {
        let mut rest = rest.to_string();
        if rest.starts_with('/') {
            rest = rest[1..].to_string();
            path = "/".into();
        }
        while !rest.is_empty() {
            let (name, remaining) = match rest.split_once('/') {
                Some((name, remaining)) => (name.to_string(), remaining.to_string()),
                None => (rest.clone(), String::new()),
            };
            rest = remaining;
            if name.is_empty() || name == "." {
                continue;
            }
            if name == ".." {
                if !path.is_empty() {
                    let (head, last) = split(&path);
                    path = head;
                    if last == ".." {
                        path = join(&join(&path, ".."), "..");
                    }
                } else {
                    path = "..".into();
                }
                continue;
            }
            let newpath = join(&path, &name);
            let is_link = std::fs::symlink_metadata(&newpath)
                .map(|meta| meta.file_type().is_symlink())
                .unwrap_or(false);
            if !is_link {
                path = newpath;
                continue;
            }
            if let Some((_, known)) = seen.iter().find(|(key, _)| *key == newpath) {
                match known {
                    Some(found) => {
                        path = found.clone();
                        continue;
                    }
                    None => return (join(&newpath, &rest), false),
                }
            }
            seen.push((newpath.clone(), None));
            let target = match std::fs::read_link(&newpath) {
                Ok(target) => target.to_string_lossy().into_owned(),
                Err(_) => {
                    path = newpath;
                    continue;
                }
            };
            let (resolved, ok) = walk(path, &target, seen);
            if !ok {
                return (join(&resolved, &rest), false);
            }
            if let Some(slot) = seen.iter_mut().find(|(key, _)| *key == newpath) {
                slot.1 = Some(resolved.clone());
            }
            path = resolved;
        }
        (path, true)
    }
    let text = path.to_string_lossy().into_owned();
    let (resolved, _) = walk(String::new(), &text, &mut Vec::new());
    let absolute = if resolved.starts_with('/') {
        resolved
    } else {
        let cwd = std::env::current_dir()
            .map(|dir| dir.to_string_lossy().into_owned())
            .unwrap_or_else(|_| "/".into());
        join(&cwd, &resolved)
    };
    PathBuf::from(posix_normpath(&absolute))
}

/// `posixpath.normpath`.
#[cfg(not(windows))]
fn posix_normpath(path: &str) -> String {
    if path.is_empty() {
        return ".".into();
    }
    let initial = if path.starts_with("//") && !path.starts_with("///") {
        2
    } else if path.starts_with('/') {
        1
    } else {
        0
    };
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part != ".." || (initial == 0 && parts.is_empty()) || parts.last() == Some(&"..") {
            parts.push(part);
        } else if !parts.is_empty() {
            parts.pop();
        }
    }
    let body = parts.join("/");
    let out = format!("{}{body}", "/".repeat(initial));
    if out.is_empty() {
        ".".into()
    } else {
        out
    }
}

pub struct FileLock {
    file: File,
}

impl FileLock {
    pub fn exclusive(path: &Path) -> Result<Self, Error> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let file = std::fs::OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(path)?;
        lock_exclusive(&file)?;
        Ok(Self { file })
    }
}

impl Drop for FileLock {
    fn drop(&mut self) {
        let _ = unlock(&self.file);
    }
}

#[cfg(unix)]
fn lock_exclusive(file: &File) -> Result<(), Error> {
    use std::os::unix::io::AsRawFd;
    const LOCK_EX: i32 = 2;
    let rc = unsafe { flock(file.as_raw_fd(), LOCK_EX) };
    if rc == 0 {
        Ok(())
    } else {
        Err(Error::last_os_error())
    }
}

#[cfg(unix)]
fn unlock(file: &File) -> Result<(), Error> {
    use std::os::unix::io::AsRawFd;
    const LOCK_UN: i32 = 8;
    let rc = unsafe { flock(file.as_raw_fd(), LOCK_UN) };
    if rc == 0 {
        Ok(())
    } else {
        Err(Error::last_os_error())
    }
}

#[cfg(unix)]
unsafe extern "C" {
    fn flock(fd: i32, op: i32) -> i32;
}

#[cfg(windows)]
fn lock_exclusive(file: &File) -> Result<(), Error> {
    use std::os::windows::io::AsRawHandle;
    let rc = unsafe {
        LockFileEx(
            file.as_raw_handle() as *mut std::ffi::c_void,
            2,
            0,
            1,
            0,
            &mut std::mem::zeroed(),
        )
    };
    if rc == 0 {
        Err(Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(windows)]
fn unlock(file: &File) -> Result<(), Error> {
    use std::os::windows::io::AsRawHandle;
    let rc = unsafe { UnlockFile(file.as_raw_handle() as *mut std::ffi::c_void, 0, 0, 1, 0) };
    if rc == 0 {
        Err(Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(windows)]
unsafe extern "system" {
    fn LockFileEx(
        file: *mut std::ffi::c_void,
        flags: u32,
        reserved: u32,
        low: u32,
        high: u32,
        overlapped: *mut std::ffi::c_void,
    ) -> i32;
    fn UnlockFile(
        file: *mut std::ffi::c_void,
        low: u32,
        high: u32,
        len_low: u32,
        len_high: u32,
    ) -> i32;
}

/// Row count for an SQLite `LIMIT ?`: a negative limit has no upper bound.
pub fn sql_limit(limit: i64) -> usize {
    if limit < 0 {
        usize::MAX
    } else {
        limit as usize
    }
}

/// `pathlib.Path.home()`: USERPROFILE on Windows, HOME elsewhere.
pub fn home_dir() -> PathBuf {
    #[cfg(windows)]
    {
        if let Ok(profile) = std::env::var("USERPROFILE") {
            return PathBuf::from(profile);
        }
        let drive = std::env::var("HOMEDRIVE").unwrap_or_default();
        let path = std::env::var("HOMEPATH").unwrap_or_default();
        PathBuf::from(format!("{drive}{path}"))
    }
    #[cfg(not(windows))]
    {
        PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/".into()))
    }
}
