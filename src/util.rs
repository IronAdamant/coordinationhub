//! Clock, paths, limits, and a process file lock. No crates.

use std::fs::File;
use std::io::Error;
use std::path::{Component, Path, PathBuf};
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
    #[cfg(windows)]
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
        }
        unsafe extern "system" {
            fn localtime_s(tm: *mut Tm, time: *const i64) -> i32;
        }
        let mut tm = unsafe { std::mem::zeroed::<Tm>() };
        unsafe {
            localtime_s(&mut tm, &secs);
        }
        format!(
            "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
            tm.tm_year + 1900,
            tm.tm_mon + 1,
            tm.tm_mday,
            tm.tm_hour,
            tm.tm_min,
            tm.tm_sec
        )
    }
}

pub fn now() -> f64 {
    if let Ok(raw) = std::env::var("COORDINATIONHUB_NOW") {
        if let Ok(value) = raw.parse::<f64>() {
            return value;
        }
    }
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

pub fn pid() -> i64 {
    if let Ok(raw) = std::env::var("COORDINATIONHUB_PID") {
        if let Ok(value) = raw.parse::<i64>() {
            return value;
        }
    }
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

pub fn soft_resolve(path: &Path) -> PathBuf {
    let joined = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(path)
    };
    let mut out = PathBuf::new();
    for component in joined.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    if out.exists() {
        if let Ok(canon) = out.canonicalize() {
            return canon;
        }
    }
    out
}

pub fn path_str(path: &Path) -> String {
    soft_resolve(path).to_string_lossy().replace('\\', "/")
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
