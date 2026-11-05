//! CPython 3.12 text-mode I/O, as the ported scripts experienced it.
//!
//! * `print()` goes through `sys.stdout`/`sys.stderr`: on Windows `\n` is
//!   written as `\r\n`; a console gets UTF-8 (via `WriteConsoleW`), a pipe
//!   or file gets the ANSI code page (`cp1252` here) with `strict` errors on
//!   stdout and `backslashreplace` on stderr. `PYTHONIOENCODING` and
//!   `PYTHONUTF8=1` override that, as in CPython. Elsewhere it is UTF-8.
//! * A non-console stdout is block-buffered and flushed at exit; stderr is
//!   written immediately (relative order only shows when both streams are
//!   captured into one pipe).
//! * `Path.read_text(encoding="utf-8")` decodes strictly and translates
//!   `\r\n` and `\r` to `\n`; `read_text()` with no encoding uses the locale
//!   code page; `write_text(..., encoding="utf-8")` writes `\n` as the
//!   platform line separator.
//! * `subprocess.run(..., capture_output=True, text=True)` decodes both
//!   streams with the locale code page and translates newlines.
//! * An uncaught exception prints a traceback and exits 1. The port prints
//!   the traceback's first line and its final `Type: message` line; the
//!   Python stack frames in between are not reproduced.

use std::io::{IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;

pub use coordinationhub::argparse::{py_isspace, py_repr};

static STDOUT_BUF: Mutex<Vec<u8>> = Mutex::new(Vec::new());

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Codec {
    Utf8,
    Cp1252,
    /// A Windows ANSI code page this port has no table for. ASCII still
    /// round-trips; anything else is reported instead of guessed.
    Other(u32),
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Errors {
    Strict,
    BackslashReplace,
}

fn utf8_mode() -> bool {
    std::env::var("PYTHONUTF8").map(|v| v == "1").unwrap_or(false)
}

/// `locale.getpreferredencoding(False)`: the codec of `open()` without an
/// encoding and of `subprocess.run(..., text=True)`.
pub fn locale_codec() -> Codec {
    if utf8_mode() {
        return Codec::Utf8;
    }
    platform_locale_codec()
}

#[cfg(windows)]
fn platform_locale_codec() -> Codec {
    extern "system" {
        fn GetACP() -> u32;
    }
    match unsafe { GetACP() } {
        1252 => Codec::Cp1252,
        65001 => Codec::Utf8,
        other => Codec::Other(other),
    }
}

#[cfg(not(windows))]
fn platform_locale_codec() -> Codec {
    Codec::Utf8
}

fn named_codec(name: &str) -> Codec {
    let norm = name.trim().to_ascii_lowercase().replace('_', "-");
    match norm.as_str() {
        "utf-8" | "utf8" | "u8" => Codec::Utf8,
        "cp1252" | "windows-1252" => Codec::Cp1252,
        _ => Codec::Other(0),
    }
}

fn stdio_codec(is_tty: bool, is_err: bool) -> (Codec, Errors) {
    let default_errors = if is_err { Errors::BackslashReplace } else { Errors::Strict };
    let mut codec = None;
    let mut errors = default_errors;
    if let Ok(value) = std::env::var("PYTHONIOENCODING") {
        let (enc, errs) = match value.split_once(':') {
            Some((enc, errs)) => (enc.to_string(), Some(errs.to_string())),
            None => (value.clone(), None),
        };
        if !enc.is_empty() {
            codec = Some(named_codec(&enc));
        }
        if let Some(errs) = errs {
            errors = match errs.as_str() {
                "backslashreplace" => Errors::BackslashReplace,
                "" => default_errors,
                _ => Errors::Strict,
            };
        }
    }
    let codec = codec.unwrap_or_else(|| {
        if utf8_mode() || (cfg!(windows) && is_tty) {
            Codec::Utf8
        } else {
            platform_locale_codec()
        }
    });
    (codec, errors)
}

// ------------------------------------------------------------------
// cp1252, as CPython's `encodings.cp1252` defines it
// ------------------------------------------------------------------

const UNDEF: u16 = 0xFFFE;
const CP1252_80: [u16; 32] = [
    0x20AC, UNDEF, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, UNDEF,
    0x017D, UNDEF, UNDEF, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A,
    0x0153, UNDEF, 0x017E, 0x0178,
];

fn cp1252_decode_byte(byte: u8) -> Option<char> {
    match byte {
        0x00..=0x7F | 0xA0..=0xFF => Some(byte as char),
        _ => {
            let code = CP1252_80[(byte - 0x80) as usize];
            if code == UNDEF {
                None
            } else {
                char::from_u32(code as u32)
            }
        }
    }
}

fn cp1252_encode_char(ch: char) -> Option<u8> {
    let code = ch as u32;
    if code < 0x80 || (0xA0..=0xFF).contains(&code) {
        return Some(code as u8);
    }
    CP1252_80
        .iter()
        .position(|mapped| *mapped != UNDEF && *mapped as u32 == code)
        .map(|index| 0x80 + index as u8)
}

fn codec_name(codec: Codec) -> &'static str {
    match codec {
        Codec::Utf8 => "utf-8",
        _ => "charmap",
    }
}

fn escape_char(ch: char) -> String {
    let code = ch as u32;
    if code <= 0xFF {
        format!("\\x{code:02x}")
    } else if code <= 0xFFFF {
        format!("\\u{code:04x}")
    } else {
        format!("\\U{code:08x}")
    }
}

/// `str.encode(codec, errors)`; `Err` is the `UnicodeEncodeError` line.
fn encode(text: &str, codec: Codec, errors: Errors) -> Result<Vec<u8>, String> {
    if codec == Codec::Utf8 {
        return Ok(text.as_bytes().to_vec());
    }
    let chars: Vec<char> = text.chars().collect();
    let one = |ch: char| -> Option<u8> {
        match codec {
            Codec::Cp1252 => cp1252_encode_char(ch),
            _ => (ch.is_ascii()).then_some(ch as u8),
        }
    };
    let mut out = Vec::with_capacity(text.len());
    let mut index = 0;
    while index < chars.len() {
        let ch = chars[index];
        if let Some(byte) = one(ch) {
            out.push(byte);
            index += 1;
            continue;
        }
        match errors {
            Errors::BackslashReplace => {
                out.extend_from_slice(escape_char(ch).as_bytes());
                index += 1;
            }
            Errors::Strict => {
                let mut end = index + 1;
                while end < chars.len() && one(chars[end]).is_none() {
                    end += 1;
                }
                let reason = match codec {
                    Codec::Other(cp) => format!("code page {cp} is not emulated by the Rust port"),
                    _ => "character maps to <undefined>".to_string(),
                };
                return Err(if end == index + 1 {
                    format!(
                        "UnicodeEncodeError: '{}' codec can't encode character '{}' in position {index}: {reason}",
                        codec_name(codec),
                        escape_char(ch)
                    )
                } else {
                    format!(
                        "UnicodeEncodeError: '{}' codec can't encode characters in position {index}-{}: {reason}",
                        codec_name(codec),
                        end - 1
                    )
                });
            }
        }
    }
    Ok(out)
}

/// `bytes.decode(codec)` (strict); `Err` is the `UnicodeDecodeError` line.
pub fn decode(bytes: &[u8], codec: Codec) -> Result<String, String> {
    match codec {
        Codec::Utf8 => decode_utf8(bytes),
        Codec::Cp1252 => {
            let mut out = String::with_capacity(bytes.len());
            for (pos, byte) in bytes.iter().enumerate() {
                match cp1252_decode_byte(*byte) {
                    Some(ch) => out.push(ch),
                    None => {
                        return Err(format!(
                            "UnicodeDecodeError: 'charmap' codec can't decode byte 0x{byte:02x} in position {pos}: character maps to <undefined>"
                        ))
                    }
                }
            }
            Ok(out)
        }
        Codec::Other(cp) => {
            if let Some(pos) = bytes.iter().position(|b| !b.is_ascii()) {
                return Err(format!(
                    "UnicodeDecodeError: 'cp{cp}' codec can't decode byte 0x{:02x} in position {pos}: code page {cp} is not emulated by the Rust port",
                    bytes[pos]
                ));
            }
            Ok(bytes.iter().map(|b| *b as char).collect())
        }
    }
}

fn decode_utf8(bytes: &[u8]) -> Result<String, String> {
    match std::str::from_utf8(bytes) {
        Ok(text) => Ok(text.to_string()),
        Err(err) => {
            let start = err.valid_up_to();
            let lead = bytes[start];
            let (end, reason) = match err.error_len() {
                None => (bytes.len(), "unexpected end of data"),
                Some(len) => {
                    let valid_lead = matches!(lead, 0xC2..=0xF4);
                    (start + len, if valid_lead { "invalid continuation byte" } else { "invalid start byte" })
                }
            };
            Err(if end == start + 1 {
                format!("UnicodeDecodeError: 'utf-8' codec can't decode byte 0x{lead:02x} in position {start}: {reason}")
            } else {
                format!(
                    "UnicodeDecodeError: 'utf-8' codec can't decode bytes in position {start}-{}: {reason}",
                    end - 1
                )
            })
        }
    }
}

/// Universal-newlines read translation: `\r\n` and `\r` become `\n`.
pub fn universal_newlines(text: &str) -> String {
    if !text.contains('\r') {
        return text.to_string();
    }
    text.replace("\r\n", "\n").replace('\r', "\n")
}

fn platform_newlines(text: &str) -> String {
    if cfg!(windows) {
        text.replace('\n', "\r\n")
    } else {
        text.to_string()
    }
}

// ------------------------------------------------------------------
// stdout / stderr
// ------------------------------------------------------------------

fn emit(is_err: bool, text: &str) {
    let translated = platform_newlines(text);
    let tty = if is_err { std::io::stderr().is_terminal() } else { std::io::stdout().is_terminal() };
    let (codec, errors) = stdio_codec(tty, is_err);
    let bytes = if cfg!(windows) && tty {
        // CPython writes a console through WriteConsoleW; so does std.
        translated.into_bytes()
    } else {
        match encode(&translated, codec, errors) {
            Ok(bytes) => bytes,
            Err(exc) => uncaught(&exc),
        }
    };
    if is_err {
        let mut err = std::io::stderr().lock();
        let _ = err.write_all(&bytes);
        let _ = err.flush();
    } else if tty {
        let mut out = std::io::stdout().lock();
        let _ = out.write_all(&bytes);
        let _ = out.flush();
    } else {
        STDOUT_BUF.lock().expect("stdout buffer").extend_from_slice(&bytes);
    }
}

/// `sys.stdout.write(text)`.
pub fn write_out(text: &str) {
    emit(false, text);
}

/// `sys.stderr.write(text)`.
pub fn write_err(text: &str) {
    emit(true, text);
}

/// `print(text)`.
pub fn print_out(text: &str) {
    write_out(text);
    write_out("\n");
}

/// `print(text, file=sys.stderr)`.
pub fn print_err(text: &str) {
    write_err(text);
    write_err("\n");
}

fn flush_stdout() {
    let mut buf = STDOUT_BUF.lock().expect("stdout buffer");
    if !buf.is_empty() {
        let mut out = std::io::stdout().lock();
        let _ = out.write_all(&buf);
        let _ = out.flush();
        buf.clear();
    }
}

/// `sys.exit(code)`: flush the buffered stdout and leave.
pub fn exit(code: i32) -> ! {
    flush_stdout();
    std::process::exit(code)
}

/// An uncaught Python exception: the traceback's first and last lines on
/// stderr, then exit status 1.
pub fn uncaught(exception_line: &str) -> ! {
    write_err("Traceback (most recent call last):\n");
    write_err(exception_line);
    write_err("\n");
    exit(1)
}

// ------------------------------------------------------------------
// Paths and files
// ------------------------------------------------------------------

/// `Path.resolve()`: absolute, symlinks resolved, without the `\\?\`
/// prefix Windows' canonical form carries.
pub fn resolve(path: &Path) -> PathBuf {
    match std::fs::canonicalize(path) {
        Ok(canonical) => strip_verbatim(canonical),
        Err(_) => std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf()),
    }
}

fn strip_verbatim(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy().to_string();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        PathBuf::from(format!(r"\\{rest}"))
    } else if let Some(rest) = text.strip_prefix(r"\\?\") {
        PathBuf::from(rest)
    } else {
        path
    }
}

/// `str(path)`.
pub fn path_str(path: &Path) -> String {
    path.to_string_lossy().to_string()
}

/// The `OSError` line for a failed file operation on `path`.
pub fn os_error(err: &std::io::Error, path: &Path) -> String {
    let shown = py_repr(&path_str(path));
    match err.kind() {
        std::io::ErrorKind::NotFound => format!("FileNotFoundError: [Errno 2] No such file or directory: {shown}"),
        std::io::ErrorKind::PermissionDenied => format!("PermissionError: [Errno 13] Permission denied: {shown}"),
        _ => match err.raw_os_error() {
            Some(21) if cfg!(unix) => format!("IsADirectoryError: [Errno 21] Is a directory: {shown}"),
            Some(code) => format!("OSError: [Errno {code}] {err}: {shown}"),
            None => format!("OSError: {err}: {shown}"),
        },
    }
}

fn read_bytes(path: &Path) -> Result<Vec<u8>, String> {
    std::fs::read(path).map_err(|err| os_error(&err, path))
}

/// `path.read_text(encoding="utf-8")`; `Err` is the exception line.
pub fn read_text_utf8(path: &Path) -> Result<String, String> {
    let bytes = read_bytes(path)?;
    Ok(universal_newlines(&decode_utf8(&bytes)?))
}

/// `path.read_text()` (locale encoding); `Err` is the exception line.
pub fn read_text_locale(path: &Path) -> Result<String, String> {
    let bytes = read_bytes(path)?;
    Ok(universal_newlines(&decode(&bytes, locale_codec())?))
}

/// `path.write_text(text, encoding="utf-8")`; `Err` is the exception line.
pub fn write_text_utf8(path: &Path, text: &str) -> Result<(), String> {
    std::fs::write(path, platform_newlines(text).as_bytes()).map_err(|err| os_error(&err, path))
}

// ------------------------------------------------------------------
// subprocess.run(cmd, cwd=..., capture_output=True, text=True)
// ------------------------------------------------------------------

pub struct Completed {
    pub returncode: i32,
    /// `None` when the output could not be decoded (Windows, see below).
    pub stdout: Option<String>,
    #[allow(dead_code)]
    pub stderr: Option<String>,
}

/// Threads CPython has created so far (`Thread-N` names).
static THREADS: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

/// Run `argv` the way the scripts' `subprocess.run` did. A program that
/// cannot be started ends the process as the uncaught Python exception
/// would. Output the locale codec cannot decode behaves per platform: on
/// Windows `communicate()` decodes in a reader thread per stream, so the
/// thread dies with its traceback on stderr and that stream comes back as
/// `None`; elsewhere decoding happens in the caller and raises.
pub fn run_text(argv: &[String], cwd: &Path) -> Completed {
    let output = Command::new(&argv[0])
        .args(&argv[1..])
        .current_dir(cwd)
        .stdin(Stdio::inherit())
        .output();
    let output = match output {
        Ok(output) => output,
        Err(err) => uncaught(&spawn_error(&err, &argv[0], cwd)),
    };
    let codec = locale_codec();
    let base = THREADS.fetch_add(2, std::sync::atomic::Ordering::SeqCst);
    let read = |bytes: &[u8], thread: usize| -> Option<String> {
        match decode(bytes, codec) {
            Ok(text) => Some(universal_newlines(&text)),
            Err(exc) if cfg!(windows) => {
                write_err(&format!(
                    "Exception in thread Thread-{thread} (_readerthread):\nTraceback (most recent call last):\n{exc}\n"
                ));
                None
            }
            Err(exc) => uncaught(&exc),
        }
    };
    let stdout = read(&output.stdout, base + 1);
    let stderr = read(&output.stderr, base + 2);
    Completed { returncode: output.status.code().unwrap_or(-1), stdout, stderr }
}

/// `None.<attr>`: the `AttributeError` a `None` stream leads to.
pub fn none_attr(attr: &str) -> ! {
    uncaught(&format!("AttributeError: 'NoneType' object has no attribute '{attr}'"))
}

fn spawn_error(err: &std::io::Error, program: &str, cwd: &Path) -> String {
    if cfg!(windows) {
        if !cwd.is_dir() {
            return "NotADirectoryError: [WinError 267] The directory name is invalid".to_string();
        }
        if err.kind() == std::io::ErrorKind::NotFound {
            return "FileNotFoundError: [WinError 2] The system cannot find the file specified".to_string();
        }
        return format!("OSError: {err}");
    }
    if !cwd.is_dir() {
        return format!("FileNotFoundError: [Errno 2] No such file or directory: {}", py_repr(&path_str(cwd)));
    }
    if err.kind() == std::io::ErrorKind::NotFound {
        return format!("FileNotFoundError: [Errno 2] No such file or directory: {}", py_repr(program));
    }
    format!("OSError: {err}")
}

// ------------------------------------------------------------------
// str helpers
// ------------------------------------------------------------------

/// `str.strip()`.
pub fn py_strip(text: &str) -> &str {
    text.trim_matches(py_isspace)
}

/// `str.splitlines()`.
pub fn py_splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut iter = text.char_indices().peekable();
    while let Some((index, ch)) = iter.next() {
        let is_break = matches!(
            ch,
            '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}'
        );
        if !is_break {
            continue;
        }
        lines.push(&text[start..index]);
        let mut next = index + ch.len_utf8();
        if ch == '\r' {
            if let Some((_, '\n')) = iter.peek() {
                iter.next();
                next += 1;
            }
        }
        start = next;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// Python's `\w` (str patterns): alphanumeric or underscore.
pub fn py_word(ch: char) -> bool {
    ch == '_' || ch.is_alphanumeric()
}

/// Python's `\d` (str patterns): a Unicode decimal digit. Only the ASCII
/// ones occur in what the scripts match; others are not treated as digits.
pub fn py_digit(ch: char) -> bool {
    ch.is_ascii_digit()
}

/// The `os.sep`-joined form of a relative path given as parts, i.e.
/// `str(Path(part0, part1, ...))`.
pub fn join_os(parts: &[String]) -> String {
    parts.join(std::path::MAIN_SEPARATOR_STR)
}
