//! Bytes from the operating system's random generator. No crates.
//!
//! - Windows: `SystemFunction036` (`RtlGenRandom`) in advapi32, which std
//!   already links on the GNU and the MSVC targets, so no import library
//!   or tool beyond the Rust toolchain is needed.
//! - Linux and Android: `getrandom(2)`, which waits until the kernel pool
//!   is seeded; `/dev/urandom` only when the kernel has no such call.
//! - macOS and iOS: `getentropy(2)`.
//! - Other Unix: `/dev/urandom`.
//!
//! `fill` fails closed: on any error the buffer's contents are unspecified
//! and the caller must not use them. There is no clock or counter fallback.

use std::io::{Error, ErrorKind};

/// Fills `buf` with random bytes, or says why the system gave none.
pub fn fill(buf: &mut [u8]) -> Result<(), Error> {
    if buf.is_empty() {
        return Ok(());
    }
    os_fill(buf)
}

/// `bytes` random bytes as lowercase hex, twice that many characters.
pub fn hex(bytes: usize) -> Result<String, Error> {
    use std::fmt::Write as _;
    let mut raw = vec![0_u8; bytes];
    fill(&mut raw)?;
    let mut out = String::with_capacity(bytes * 2);
    for byte in &raw {
        let _ = write!(out, "{byte:02x}");
    }
    Ok(out)
}

#[cfg(windows)]
fn os_fill(buf: &mut [u8]) -> Result<(), Error> {
    #[link(name = "advapi32")]
    extern "system" {
        // BOOLEAN RtlGenRandom(PVOID RandomBuffer, ULONG RandomBufferLength)
        fn SystemFunction036(buffer: *mut u8, length: u32) -> u8;
    }
    for chunk in buf.chunks_mut(1 << 20) {
        // SAFETY: `chunk` is a valid, writable region of `chunk.len()`
        // bytes, and the length fits in a u32.
        let ok = unsafe { SystemFunction036(chunk.as_mut_ptr(), chunk.len() as u32) };
        if ok == 0 {
            return Err(Error::new(ErrorKind::Other, "RtlGenRandom failed"));
        }
    }
    Ok(())
}

#[cfg(any(target_os = "linux", target_os = "android"))]
fn os_fill(buf: &mut [u8]) -> Result<(), Error> {
    extern "C" {
        fn getrandom(buf: *mut u8, len: usize, flags: u32) -> isize;
    }
    const ENOSYS: i32 = 38;
    let mut done = 0;
    while done < buf.len() {
        // SAFETY: the pointer and length describe the unfilled rest of `buf`.
        let got = unsafe { getrandom(buf[done..].as_mut_ptr(), buf.len() - done, 0) };
        if got > 0 {
            done += got as usize;
            continue;
        }
        let err = Error::last_os_error();
        match err.kind() {
            ErrorKind::Interrupted => continue,
            // A kernel older than 3.17 has no getrandom(2).
            _ if err.raw_os_error() == Some(ENOSYS) => return dev_urandom(buf),
            _ => return Err(err),
        }
    }
    Ok(())
}

#[cfg(any(target_os = "macos", target_os = "ios"))]
fn os_fill(buf: &mut [u8]) -> Result<(), Error> {
    extern "C" {
        fn getentropy(buf: *mut u8, len: usize) -> i32;
    }
    // getentropy(2) hands out at most 256 bytes per call.
    for chunk in buf.chunks_mut(256) {
        // SAFETY: `chunk` is a valid, writable region of `chunk.len()` bytes.
        if unsafe { getentropy(chunk.as_mut_ptr(), chunk.len()) } != 0 {
            return Err(Error::last_os_error());
        }
    }
    Ok(())
}

#[cfg(all(
    unix,
    not(any(target_os = "linux", target_os = "android", target_os = "macos", target_os = "ios"))
))]
fn os_fill(buf: &mut [u8]) -> Result<(), Error> {
    dev_urandom(buf)
}

#[cfg(all(unix, not(any(target_os = "macos", target_os = "ios"))))]
fn dev_urandom(buf: &mut [u8]) -> Result<(), Error> {
    use std::io::Read;
    std::fs::File::open("/dev/urandom")?.read_exact(buf)
}

#[cfg(not(any(unix, windows)))]
fn os_fill(_buf: &mut [u8]) -> Result<(), Error> {
    Err(Error::new(
        ErrorKind::Unsupported,
        "no operating system random generator is known for this platform",
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fills_every_byte_and_does_not_repeat() {
        // 64 bytes: all zero, or two equal draws, has probability 2^-512.
        let mut first = [0_u8; 64];
        let mut second = [0_u8; 64];
        fill(&mut first).unwrap();
        fill(&mut second).unwrap();
        assert_ne!(first, [0_u8; 64]);
        assert_ne!(first, second);
        // No half of the buffer is left untouched (the 1.2.0 token was a
        // timestamp followed by sixteen zero characters).
        assert_ne!(first[..32], [0_u8; 32]);
        assert_ne!(first[32..], [0_u8; 32]);
    }

    #[test]
    fn large_and_empty_requests_work() {
        fill(&mut []).unwrap();
        let mut big = vec![0_u8; 3000];
        fill(&mut big).unwrap();
        // Each 256-byte chunk (the macOS call size) got bytes.
        for chunk in big.chunks(256) {
            assert!(chunk.iter().any(|byte| *byte != 0));
        }
    }

    #[test]
    fn hex_is_lowercase_and_twice_as_long() {
        let token = hex(16).unwrap();
        assert_eq!(token.len(), 32);
        assert!(token.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')));
        assert_ne!(token, hex(16).unwrap());
    }
}
