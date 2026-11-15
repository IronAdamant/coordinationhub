//! Hand-rolled JSON. Object key order is insertion order.

use std::collections::BTreeMap;
use std::fmt::Write as _;

#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    String(String),
    Array(Vec<Value>),
    Object(Vec<(String, Value)>),
}

impl Value {
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Object(pairs) => pairs.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::String(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Value::Bool(b) => Some(*b),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Value::Int(n) => Some(*n),
            Value::Float(n)
                if n.fract() == 0.0 && *n >= i64::MIN as f64 && *n <= i64::MAX as f64 =>
            {
                Some(*n as i64)
            }
            _ => None,
        }
    }

    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Value::Int(n) => Some(*n as f64),
            Value::Float(n) => Some(*n),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&[Value]> {
        match self {
            Value::Array(items) => Some(items),
            _ => None,
        }
    }

    pub fn as_object(&self) -> Option<&[(String, Value)]> {
        match self {
            Value::Object(pairs) => Some(pairs),
            _ => None,
        }
    }

    pub fn py_type_name(&self) -> &'static str {
        match self {
            Value::Null => "NoneType",
            Value::Bool(_) => "bool",
            Value::Int(_) => "int",
            Value::Float(_) => "float",
            Value::String(_) => "str",
            Value::Array(_) => "list",
            Value::Object(_) => "dict",
        }
    }
}

pub fn null() -> Value {
    Value::Null
}
pub fn b(v: bool) -> Value {
    Value::Bool(v)
}
pub fn i(v: i64) -> Value {
    Value::Int(v)
}
pub fn f(v: f64) -> Value {
    Value::Float(v)
}
pub fn s(v: impl Into<String>) -> Value {
    Value::String(v.into())
}
pub fn arr(items: Vec<Value>) -> Value {
    Value::Array(items)
}
pub fn obj(pairs: Vec<(&str, Value)>) -> Value {
    Value::Object(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}
pub fn opt_str(v: Option<&str>) -> Value {
    match v {
        Some(text) => s(text),
        None => null(),
    }
}
pub fn opt_string(v: &Option<String>) -> Value {
    match v {
        Some(text) => s(text.clone()),
        None => null(),
    }
}
pub fn opt_f(v: Option<f64>) -> Value {
    match v {
        Some(n) => f(n),
        None => null(),
    }
}

pub fn opt_i(v: Option<i64>) -> Value {
    match v {
        Some(n) => i(n),
        None => null(),
    }
}

/// The largest magnitude a number may have in a tool call or in a stored
/// row. Seconds this large are about 31 million years, so every TTL,
/// timeout and timestamp fits, and sums and products of accepted values
/// (`now + ttl`, seconds to milliseconds) stay far inside a double.
pub const MAX_NUMBER: f64 = 1e15;

/// `n` as a number that can be stored: NaN becomes 0, and anything beyond
/// `MAX_NUMBER` (the infinities included) is pulled back to it.
pub fn finite(n: f64) -> f64 {
    if n.is_nan() {
        0.0
    } else {
        n.clamp(-MAX_NUMBER, MAX_NUMBER)
    }
}

/// Arrays and objects may nest this deep. A request nested deeper is
/// refused before anything recurses on it: the parser is the only code that
/// meets untrusted depth, and the writers, the schema check and `Drop` then
/// walk values it has already bounded.
pub const MAX_DEPTH: usize = 128;

/// Strict RFC 8259 JSON: one value, nothing after it, numbers and strings
/// by the grammar, every number finite, no duplicate object keys, nesting
/// no deeper than `MAX_DEPTH`. Each error names the byte offset.
pub fn parse(input: &str) -> Result<Value, String> {
    let mut parser = Parser::new(input, false);
    parser.document()
}

/// Stored JSON text may nest this deep when it is read back. 1.2.0 and
/// earlier stored whatever nesting a request had, so the limit is well
/// above `MAX_DEPTH`. It still bounds the stack: a test reads, copies,
/// writes and drops the deepest value on 256 KiB (1 MiB, the Windows main
/// thread, in an unoptimised build). Deeper text is returned as text.
pub const STORED_MAX_DEPTH: usize = 320;

/// Reads JSON text that an earlier call, or an earlier version, stored in
/// a row: a message payload, an event payload, a list of agents. Requests
/// are held to `parse`; what is already in the store must stay readable,
/// so this reader takes what older versions could write:
///
/// - nesting up to `STORED_MAX_DEPTH`;
/// - a repeated object key, where the last value wins (as in 0.8.0);
/// - `NaN`, `Infinity`, `-Infinity`, which come back as non-finite floats
///   (`has_non_finite` finds them; the writers print them as `null`).
///
/// Everything else is as strict as `parse`.
pub fn parse_stored(input: &str) -> Result<Value, String> {
    let mut parser = Parser::new(input, true);
    parser.max_depth = STORED_MAX_DEPTH;
    parser.last_key_wins = true;
    parser.document()
}

/// True when `value` holds a float that is not finite anywhere inside.
pub fn has_non_finite(value: &Value) -> bool {
    match value {
        Value::Float(n) => !n.is_finite(),
        Value::Array(items) => items.iter().any(has_non_finite),
        Value::Object(pairs) => pairs.iter().any(|(_, item)| has_non_finite(item)),
        _ => false,
    }
}

/// A stored JSON document for a reply: the value, or, when it cannot be
/// shown as JSON (it is not JSON, nests deeper than `STORED_MAX_DEPTH`, or
/// holds a number that is not finite), the reason. The caller then returns
/// the stored text itself and says so, instead of dropping the document.
pub fn stored_document(raw: &str) -> Result<Value, String> {
    let value = parse_stored(raw)?;
    if has_non_finite(&value) {
        return Err("it holds a number that is not finite (NaN or Infinity)".into());
    }
    Ok(value)
}

/// `parse` for a line of the journal. It differs in one way: the bare words
/// `NaN`, `Infinity` and `-Infinity`, and a number too large for a double,
/// are read instead of refused, because 1.2.0 could write them. The second
/// value is true when the line held one, so the caller can repair the row.
pub fn parse_journal(input: &str) -> Result<(Value, bool), String> {
    let mut parser = Parser::new(input, true);
    let value = parser.document()?;
    Ok((value, parser.repaired))
}

struct Parser<'a> {
    bytes: &'a [u8],
    index: usize,
    depth: usize,
    /// Journal mode: read the non-finite numbers 1.2.0 wrote.
    lenient: bool,
    repaired: bool,
    max_depth: usize,
    /// Stored text: a repeated key replaces the earlier value.
    last_key_wins: bool,
}

impl<'a> Parser<'a> {
    fn new(input: &'a str, lenient: bool) -> Self {
        Parser {
            bytes: input.as_bytes(),
            index: 0,
            depth: 0,
            lenient,
            repaired: false,
            max_depth: MAX_DEPTH,
            last_key_wins: false,
        }
    }

    fn document(&mut self) -> Result<Value, String> {
        self.skip();
        let value = self.parse_value()?;
        self.skip();
        if self.index != self.bytes.len() {
            return Err(self.fail("trailing data after the JSON value; send exactly one value"));
        }
        Ok(value)
    }

    fn fail(&self, what: &str) -> String {
        format!("{what} (at byte {})", self.index)
    }

    /// JSON whitespace only: space, tab, line feed, carriage return.
    fn skip(&mut self) {
        while let Some(b' ' | b'\t' | b'\n' | b'\r') = self.bytes.get(self.index) {
            self.index += 1;
        }
    }

    fn parse_value(&mut self) -> Result<Value, String> {
        self.skip();
        let Some(&byte) = self.bytes.get(self.index) else {
            return Err(self.fail("unexpected end of JSON"));
        };
        match byte {
            b'n' => self.consume_lit(b"null", Value::Null),
            b't' => self.consume_lit(b"true", Value::Bool(true)),
            b'f' => self.consume_lit(b"false", Value::Bool(false)),
            b'"' => Ok(Value::String(self.parse_string()?)),
            b'[' | b'{' => {
                if self.depth >= self.max_depth {
                    return Err(self.fail(&format!(
                        "JSON is nested deeper than {} levels; flatten the value",
                        self.max_depth
                    )));
                }
                self.depth += 1;
                let value = if byte == b'[' {
                    self.parse_array()
                } else {
                    self.parse_object()
                };
                self.depth -= 1;
                value
            }
            b'N' | b'I' => self.non_finite(),
            b'-' if self.bytes.get(self.index + 1) == Some(&b'I') => self.non_finite(),
            b'-' | b'0'..=b'9' => self.parse_number(),
            _ => Err(self.fail(&format!("unexpected JSON byte {}", show_byte(byte)))),
        }
    }

    /// `NaN`, `Infinity`, `-Infinity`: not JSON. Journal mode reads them so a
    /// store 1.2.0 wrote them into still opens.
    fn non_finite(&mut self) -> Result<Value, String> {
        let rest = &self.bytes[self.index..];
        let (word, value) = if rest.starts_with(b"NaN") {
            ("NaN", f64::NAN)
        } else if rest.starts_with(b"Infinity") {
            ("Infinity", f64::INFINITY)
        } else if rest.starts_with(b"-Infinity") {
            ("-Infinity", f64::NEG_INFINITY)
        } else {
            return Err(self.fail(&format!("unexpected JSON byte {}", show_byte(rest[0]))));
        };
        if !self.lenient {
            return Err(self.fail(&format!(
                "{word} is not a JSON number; send a finite number"
            )));
        }
        self.index += word.len();
        self.repaired = true;
        Ok(Value::Float(value))
    }

    fn consume_lit(&mut self, lit: &[u8], value: Value) -> Result<Value, String> {
        if self.bytes[self.index..].starts_with(lit) {
            self.index += lit.len();
            Ok(value)
        } else {
            Err(self.fail("invalid JSON literal (expected null, true or false)"))
        }
    }

    fn parse_array(&mut self) -> Result<Value, String> {
        self.index += 1;
        let mut items = Vec::new();
        self.skip();
        if self.bytes.get(self.index) == Some(&b']') {
            self.index += 1;
            return Ok(Value::Array(items));
        }
        loop {
            items.push(self.parse_value()?);
            self.skip();
            match self.bytes.get(self.index) {
                Some(&b',') => {
                    self.index += 1;
                }
                Some(&b']') => {
                    self.index += 1;
                    break;
                }
                _ => return Err(self.fail("expected ',' or ']' in array")),
            }
        }
        Ok(Value::Array(items))
    }

    fn parse_object(&mut self) -> Result<Value, String> {
        self.index += 1;
        let mut pairs: Vec<(String, Value)> = Vec::new();
        self.skip();
        if self.bytes.get(self.index) == Some(&b'}') {
            self.index += 1;
            return Ok(Value::Object(pairs));
        }
        // Small objects compare keys directly; large ones use a set, so a
        // request with many keys cannot cost quadratic time.
        let mut seen: Option<std::collections::HashSet<String>> = None;
        loop {
            self.skip();
            if self.bytes.get(self.index) != Some(&b'"') {
                return Err(self.fail("object key must be a string"));
            }
            let key = self.parse_string()?;
            let duplicate = match &mut seen {
                Some(set) => !set.insert(key.clone()),
                None => {
                    let found = pairs.iter().any(|(name, _)| *name == key);
                    if pairs.len() >= 16 {
                        let mut set: std::collections::HashSet<String> =
                            pairs.iter().map(|(name, _)| name.clone()).collect();
                        set.insert(key.clone());
                        seen = Some(set);
                    }
                    found
                }
            };
            if duplicate && !self.last_key_wins {
                let mut shown = String::new();
                write_string(&mut shown, &key, true);
                return Err(self.fail(&format!(
                    "duplicate object key {shown}; send each key once"
                )));
            }
            self.skip();
            if self.bytes.get(self.index) != Some(&b':') {
                return Err(self.fail("expected ':' after object key"));
            }
            self.index += 1;
            let value = self.parse_value()?;
            match pairs.iter_mut().find(|(name, _)| duplicate && *name == key) {
                Some(pair) => pair.1 = value,
                None => pairs.push((key, value)),
            }
            self.skip();
            match self.bytes.get(self.index) {
                Some(&b',') => self.index += 1,
                Some(&b'}') => {
                    self.index += 1;
                    break;
                }
                _ => return Err(self.fail("expected ',' or '}' in object")),
            }
        }
        Ok(Value::Object(pairs))
    }

    fn parse_string(&mut self) -> Result<String, String> {
        self.index += 1;
        let mut out = String::new();
        while let Some(&byte) = self.bytes.get(self.index) {
            self.index += 1;
            match byte {
                b'"' => return Ok(out),
                b'\\' => {
                    let Some(&esc) = self.bytes.get(self.index) else {
                        return Err(self.fail("unterminated string"));
                    };
                    self.index += 1;
                    match esc {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{0008}'),
                        b'f' => out.push('\u{000c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let mut code = self.read_hex4()?;
                            if (0xD800..=0xDBFF).contains(&code)
                                && self.bytes.get(self.index) == Some(&b'\\')
                                && self.bytes.get(self.index + 1) == Some(&b'u')
                            {
                                self.index += 2;
                                let low = self.read_hex4()?;
                                if (0xDC00..=0xDFFF).contains(&low) {
                                    code = 0x10000 + (((code - 0xD800) << 10) | (low - 0xDC00));
                                } else {
                                    return Err(self.fail("\\u escape is half of a surrogate pair"));
                                }
                            }
                            match char::from_u32(code) {
                                Some(ch) => out.push(ch),
                                None => {
                                    return Err(self.fail("\\u escape is half of a surrogate pair"))
                                }
                            }
                        }
                        _ => return Err(self.fail("unknown string escape")),
                    }
                }
                byte if byte < 0x20 => {
                    self.index -= 1;
                    return Err(self.fail(&format!(
                        "raw control character 0x{byte:02x} in a string; write it as an escape such as \\n or \\u{byte:04x}"
                    )));
                }
                byte if byte < 0x80 => out.push(byte as char),
                byte => {
                    let width = match byte {
                        0xC2..=0xDF => 2,
                        0xE0..=0xEF => 3,
                        0xF0..=0xF4 => 4,
                        _ => return Err(self.fail("invalid utf-8 in string")),
                    };
                    let start = self.index - 1;
                    if start + width > self.bytes.len() {
                        return Err(self.fail("invalid utf-8 in string"));
                    }
                    let slice = &self.bytes[start..start + width];
                    let Ok(text) = std::str::from_utf8(slice) else {
                        return Err(self.fail("invalid utf-8 in string"));
                    };
                    out.push_str(text);
                    self.index = start + width;
                }
            }
        }
        Err(self.fail("unterminated string"))
    }

    fn read_hex4(&mut self) -> Result<u32, String> {
        let Some(digits) = self.bytes.get(self.index..self.index + 4) else {
            return Err(self.fail("short \\u escape (four hex digits expected)"));
        };
        let mut code = 0_u32;
        for &digit in digits {
            let Some(nibble) = (digit as char).to_digit(16) else {
                return Err(self.fail("bad \\u escape (four hex digits expected)"));
            };
            code = code * 16 + nibble;
        }
        self.index += 4;
        Ok(code)
    }

    fn digits(&mut self) -> usize {
        let start = self.index;
        while matches!(self.bytes.get(self.index), Some(b'0'..=b'9')) {
            self.index += 1;
        }
        self.index - start
    }

    /// RFC 8259 `number`: `-? (0 | [1-9][0-9]*) (. [0-9]+)? ([eE] [+-]? [0-9]+)?`.
    fn parse_number(&mut self) -> Result<Value, String> {
        let start = self.index;
        if self.bytes.get(self.index) == Some(&b'-') {
            self.index += 1;
        }
        let int_start = self.index;
        let int_len = self.digits();
        if int_len == 0 {
            return Err(self.fail(
                "a number needs a digit before any '.' or exponent (write 0.5, not .5)",
            ));
        }
        if int_len > 1 && self.bytes[int_start] == b'0' {
            self.index = int_start;
            return Err(self.fail("a number may not have a leading zero (write 1, not 01)"));
        }
        let mut is_float = false;
        if self.bytes.get(self.index) == Some(&b'.') {
            is_float = true;
            self.index += 1;
            if self.digits() == 0 {
                return Err(self.fail("a number needs a digit after '.' (write 1.0, not 1.)"));
            }
        }
        if matches!(self.bytes.get(self.index), Some(b'e' | b'E')) {
            is_float = true;
            self.index += 1;
            if matches!(self.bytes.get(self.index), Some(b'+' | b'-')) {
                self.index += 1;
            }
            if self.digits() == 0 {
                return Err(self.fail("a number needs a digit in its exponent"));
            }
        }
        // The bytes are ASCII digits, signs, '.', 'e': valid UTF-8.
        let text = std::str::from_utf8(&self.bytes[start..self.index]).unwrap_or("");
        if is_float {
            let Ok(n) = text.parse::<f64>() else {
                return Err(self.fail("bad number"));
            };
            if !n.is_finite() {
                if !self.lenient {
                    self.index = start;
                    return Err(self.fail(&format!(
                        "number {} is too large for a 64-bit float; send a finite number",
                        clip(text)
                    )));
                }
                self.repaired = true;
            }
            Ok(Value::Float(n))
        } else {
            match text.parse::<i64>() {
                Ok(n) => Ok(Value::Int(n)),
                Err(_) => {
                    self.index = start;
                    Err(self.fail(&format!(
                        "integer {} does not fit in 64 bits; send a smaller number",
                        clip(text)
                    )))
                }
            }
        }
    }
}

/// A byte for an error message: the character when printable, else hex.
fn show_byte(byte: u8) -> String {
    if byte.is_ascii_graphic() {
        format!("'{}'", byte as char)
    } else {
        format!("0x{byte:02x}")
    }
}

/// At most 24 characters of a number, for an error message.
fn clip(text: &str) -> String {
    if text.len() <= 24 {
        text.to_string()
    } else {
        format!("{}...", &text[..24])
    }
}

pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    write_compact(&mut out, value);
    out
}

/// JSON with no spaces after separators and non-ASCII text left as UTF-8
/// (the frame around it is still ASCII): the smallest text for a reply.
pub fn dumps_tight(value: &Value) -> String {
    let mut out = String::new();
    write_tight(&mut out, value);
    out
}

fn write_tight(out: &mut String, value: &Value) {
    match value {
        Value::Array(items) => {
            out.push('[');
            for (idx, item) in items.iter().enumerate() {
                if idx > 0 {
                    out.push(',');
                }
                write_tight(out, item);
            }
            out.push(']');
        }
        Value::Object(pairs) => {
            out.push('{');
            for (idx, (key, item)) in pairs.iter().enumerate() {
                if idx > 0 {
                    out.push(',');
                }
                write_string(out, key, false);
                out.push(':');
                write_tight(out, item);
            }
            out.push('}');
        }
        Value::String(text) => write_string(out, text, false),
        other => write_compact(out, other),
    }
}

pub fn dumps_pretty(value: &Value) -> String {
    let mut out = String::new();
    write_pretty(&mut out, value, 0);
    out
}

fn write_compact(out: &mut String, value: &Value) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(n) => {
            let _ = write!(out, "{n}");
        }
        Value::Float(n) => write_float(out, *n),
        Value::String(text) => write_string(out, text, true),
        Value::Array(items) => {
            out.push('[');
            for (idx, item) in items.iter().enumerate() {
                if idx > 0 {
                    out.push_str(", ");
                }
                write_compact(out, item);
            }
            out.push(']');
        }
        Value::Object(pairs) => {
            out.push('{');
            for (idx, (key, item)) in pairs.iter().enumerate() {
                if idx > 0 {
                    out.push_str(", ");
                }
                write_string(out, key, true);
                out.push_str(": ");
                write_compact(out, item);
            }
            out.push('}');
        }
    }
}

fn write_pretty(out: &mut String, value: &Value, indent: usize) {
    match value {
        Value::Array(items) if !items.is_empty() => {
            out.push_str("[\n");
            for (idx, item) in items.iter().enumerate() {
                pad(out, indent + 1);
                write_pretty(out, item, indent + 1);
                if idx + 1 != items.len() {
                    out.push(',');
                }
                out.push('\n');
            }
            pad(out, indent);
            out.push(']');
        }
        Value::Object(pairs) if !pairs.is_empty() => {
            out.push_str("{\n");
            for (idx, (key, item)) in pairs.iter().enumerate() {
                pad(out, indent + 1);
                write_string(out, key, true);
                out.push_str(": ");
                write_pretty(out, item, indent + 1);
                if idx + 1 != pairs.len() {
                    out.push(',');
                }
                out.push('\n');
            }
            pad(out, indent);
            out.push('}');
        }
        other => write_compact(out, other),
    }
}

fn pad(out: &mut String, indent: usize) {
    for _ in 0..indent {
        out.push_str("  ");
    }
}

/// `json.dumps` float text: `float.__repr__`. A value that is not finite
/// is written as `null`: `NaN` and `Infinity` are not JSON, no reader of
/// this output could parse them, and 1.2.0 bricked its own journal by
/// writing them. Tool arguments are checked before they are used
/// (`validate::check_numbers`) and the journal clamps its numbers
/// (`db::finite`), so `null` here is the last line of defence, not the
/// usual path.
fn write_float(out: &mut String, n: f64) {
    if n.is_finite() {
        out.push_str(&crate::argparse::py_float_repr(n));
    } else {
        out.push_str("null");
    }
}

fn write_string(out: &mut String, text: &str, ascii: bool) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{0008}' => out.push_str("\\b"),
            '\u{000c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c if ascii && (c as u32) > 0xFFFF => {
                let code = (c as u32) - 0x10000;
                let high = 0xD800 + (code >> 10);
                let low = 0xDC00 + (code & 0x3FF);
                let _ = write!(out, "\\u{high:04x}\\u{low:04x}");
            }
            c if ascii && (c as u32) > 0x7f => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

pub fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Int(n) => n.to_string(),
        Value::Float(n) => crate::argparse::py_float_repr(*n),
        Value::String(text) => crate::argparse::py_repr(text),
        Value::Array(items) => {
            let parts: Vec<String> = items.iter().map(py_repr).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Object(pairs) => {
            let parts: Vec<String> = pairs
                .iter()
                .map(|(key, item)| format!("{}: {}", crate::argparse::py_repr(key), py_repr(item)))
                .collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

pub fn string_list(values: &[String]) -> Value {
    arr(values.iter().cloned().map(Value::String).collect())
}

pub fn sorted_strings(values: impl IntoIterator<Item = String>) -> Value {
    let mut items: Vec<String> = values.into_iter().collect();
    items.sort();
    string_list(&items)
}

/// Lookup that treats an explicit null the same as a missing key.
pub fn field<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    match value.get(key) {
        Some(Value::Null) | None => None,
        Some(other) => Some(other),
    }
}

pub fn object_map(value: &Value) -> BTreeMap<String, Value> {
    let mut map = BTreeMap::new();
    if let Value::Object(pairs) = value {
        for (key, item) in pairs {
            map.insert(key.clone(), item.clone());
        }
    }
    map
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_object() {
        let raw = r#"{"a":1,"b":1.5,"c":"hi","d":[true,null]}"#;
        let parsed = parse(raw).unwrap();
        assert_eq!(
            dumps(&parsed),
            r#"{"a": 1, "b": 1.5, "c": "hi", "d": [true, null]}"#
        );
    }

    #[test]
    fn whole_float_keeps_decimal() {
        assert_eq!(dumps(&f(300.0)), "300.0");
    }

    fn err(raw: &str) -> String {
        parse(raw).expect_err(raw)
    }

    #[test]
    fn numbers_that_are_not_finite_are_refused() {
        assert!(err("1e400").contains("too large for a 64-bit float"), "{}", err("1e400"));
        assert!(err("-1e400").contains("too large"));
        assert!(err("{\"ttl\": 1e999}").contains("(at byte 8)"), "{}", err("{\"ttl\": 1e999}"));
        for word in ["NaN", "Infinity", "-Infinity"] {
            assert!(err(word).contains("is not a JSON number"), "{}", err(word));
        }
        // The largest and smallest doubles still parse.
        assert_eq!(parse("1.7976931348623157e308").unwrap(), f(f64::MAX));
        assert_eq!(parse("1e-400").unwrap(), f(0.0));
    }

    #[test]
    fn the_writer_never_emits_a_non_finite_number() {
        for n in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let value = obj(vec![("expires_at", f(n)), ("list", arr(vec![f(n)]))]);
            for text in [dumps(&value), dumps_tight(&value), dumps_pretty(&value)] {
                assert!(!text.contains("NaN") && !text.contains("Infinity"), "{text}");
                assert_eq!(parse(&text).unwrap().get("expires_at"), Some(&Value::Null), "{text}");
            }
        }
        assert_eq!(finite(f64::INFINITY), MAX_NUMBER);
        assert_eq!(finite(f64::NEG_INFINITY), -MAX_NUMBER);
        assert_eq!(finite(f64::NAN), 0.0);
        assert_eq!(finite(12.5), 12.5);
    }

    #[test]
    fn the_journal_reader_accepts_what_1_2_0_wrote() {
        let (value, repaired) = parse_journal("{\"lock_ttl\": Infinity, \"a\": -Infinity, \"b\": NaN, \"c\": 1e400}").unwrap();
        assert!(repaired);
        assert_eq!(value.get("lock_ttl").and_then(Value::as_f64), Some(f64::INFINITY));
        assert_eq!(value.get("a").and_then(Value::as_f64), Some(f64::NEG_INFINITY));
        assert!(value.get("b").and_then(Value::as_f64).unwrap().is_nan());
        assert_eq!(value.get("c").and_then(Value::as_f64), Some(f64::INFINITY));
        let (_, repaired) = parse_journal("{\"lock_ttl\": 300.0}").unwrap();
        assert!(!repaired);
        // Nothing else is relaxed.
        assert!(parse_journal("{\"a\": Infinite}").is_err());
        assert!(parse_journal("{\"a\": 01}").is_err());
    }

    #[test]
    fn stored_text_is_read_tolerantly() {
        // What 1.2.0 stored for a payload with a repeated key: both pairs.
        assert_eq!(parse_stored("{\"k\": 1, \"k\": 2}").unwrap(), obj(vec![("k", i(2))]));
        assert_eq!(
            parse_stored("{\"a\": 1, \"k\": 1, \"b\": 2, \"k\": {\"k\": 3, \"k\": 4}}").unwrap(),
            obj(vec![("a", i(1)), ("k", obj(vec![("k", i(4))])), ("b", i(2))])
        );
        // Deeper than a request may be, up to the stored limit.
        let deep = |n: usize| format!("{}{}", "[".repeat(n), "]".repeat(n));
        assert!(parse(&deep(300)).is_err());
        assert!(parse_stored(&deep(300)).is_ok());
        assert!(parse_stored(&deep(STORED_MAX_DEPTH)).is_ok());
        let message = parse_stored(&deep(STORED_MAX_DEPTH + 1)).unwrap_err();
        assert!(message.contains("nested deeper than 320 levels"), "{message}");
        assert!(parse_stored(&deep(100_000)).is_err());
        // Numbers that are not finite are read and can be found.
        let value = parse_stored("{\"a\": [1, {\"b\": Infinity}]}").unwrap();
        assert!(has_non_finite(&value));
        assert!(!has_non_finite(&parse_stored("{\"a\": [1, {\"b\": 2.5}]}").unwrap()));
        assert!(stored_document("{\"a\": NaN}").unwrap_err().contains("not finite"));
        assert_eq!(stored_document("{\"a\": 1}").unwrap(), obj(vec![("a", i(1))]));
        // The grammar itself is not relaxed.
        assert!(parse_stored("{\"a\": 01}").is_err());
        assert!(parse_stored("{\"a\": 1} x").is_err());
    }

    /// The deepest stored value is parsed, checked, copied, written and
    /// dropped on a small stack: 256 KiB, an eighth of a connection
    /// thread's (1 MiB without optimisation: the smallest stack the hub
    /// runs on, the main thread on Windows).
    #[test]
    fn the_deepest_stored_value_fits_a_small_stack() {
        let text = format!("{}1{}", "{\"k\":[".repeat(STORED_MAX_DEPTH / 2), "]}".repeat(STORED_MAX_DEPTH / 2));
        let worker = std::thread::Builder::new()
            .stack_size(if cfg!(debug_assertions) { 1024 * 1024 } else { 256 * 1024 })
            .spawn(move || {
                let value = parse_stored(&text).unwrap();
                assert!(!has_non_finite(&value));
                let copy = value.clone();
                assert_eq!(copy, value);
                let written = dumps_tight(&copy);
                assert_eq!(written, text);
                // The other two writers walk it as well.
                assert!(dumps(&value).len() > written.len());
                assert!(dumps_pretty(&value).len() > written.len());
                written.len()
            })
            .unwrap();
        assert!(worker.join().unwrap() > STORED_MAX_DEPTH);
    }

    #[test]
    fn nesting_is_limited() {
        let deep = |n: usize| format!("{}{}", "[".repeat(n), "]".repeat(n));
        assert!(parse(&deep(MAX_DEPTH)).is_ok());
        let message = err(&deep(MAX_DEPTH + 1));
        assert!(message.contains("nested deeper than 128 levels"), "{message}");
        // 100,000 levels used to overflow the stack; now it is one error.
        let huge = format!("{}1{}", "{\"a\":".repeat(100_000), "}".repeat(100_000));
        assert!(err(&huge).contains("nested deeper than 128 levels"));
        assert!(err(&"[".repeat(100_000)).contains("nested deeper than 128 levels"));
        // Depth counts open containers, not siblings.
        let wide = format!("[{}]", vec!["[[]]"; 5000].join(","));
        assert!(parse(&wide).is_ok());
    }

    #[test]
    fn numbers_follow_rfc_8259() {
        for (raw, why) in [
            ("01", "leading zero"),
            ("-01", "leading zero"),
            ("00", "leading zero"),
            ("1.", "digit after '.'"),
            ("-.5", "digit before"),
            (".5", "unexpected JSON byte"),
            ("1e", "digit in its exponent"),
            ("1e+", "digit in its exponent"),
            ("-", "digit before"),
            ("+1", "unexpected JSON byte"),
            ("0x10", "trailing data"),
            ("1.e5", "digit after '.'"),
            ("9223372036854775808", "does not fit in 64 bits"),
        ] {
            assert!(err(raw).contains(why), "{raw}: {}", err(raw));
        }
        assert_eq!(parse("0").unwrap(), i(0));
        assert_eq!(parse("-0").unwrap(), i(0));
        assert_eq!(parse("-0.0").unwrap(), f(-0.0));
        assert_eq!(parse("10").unwrap(), i(10));
        assert_eq!(parse("0.5").unwrap(), f(0.5));
        assert_eq!(parse("1E+2").unwrap(), f(100.0));
        assert_eq!(parse("-9223372036854775808").unwrap(), i(i64::MIN));
    }

    #[test]
    fn strings_follow_rfc_8259() {
        for code in 0_u8..0x20 {
            let raw = format!("\"a{}b\"", code as char);
            let message = err(&raw);
            assert!(message.contains(&format!("raw control character 0x{code:02x}")), "{message}");
        }
        assert_eq!(parse("\"a\\n\\t\\u0001b\"").unwrap(), s("a\n\t\u{0001}b"));
        assert_eq!(parse("\"\\ud83d\\ude00\"").unwrap(), s("\u{1f600}"));
        assert!(err("\"\\ud83d\"").contains("surrogate"));
        assert!(err("\"\\ude00\"").contains("surrogate"));
        assert!(err("\"\\u12\"").contains("\\u escape"));
        assert!(err("\"\\u12g4\"").contains("\\u escape"));
        assert!(err("\"\\x41\"").contains("unknown string escape"));
        assert!(err("\"abc").contains("unterminated string"));
        assert!(err("\"abc\\").contains("unterminated string"));
        // Every string the writer produces reads back.
        let odd = "\u{0000}\u{001f}\"\\/\u{007f}\u{0080}\u{2028}\u{1f600}";
        assert_eq!(parse(&dumps(&s(odd))).unwrap(), s(odd));
        assert_eq!(parse(&dumps_tight(&s(odd))).unwrap(), s(odd));
    }

    #[test]
    fn one_value_and_nothing_after_it() {
        assert!(err("{} {}").contains("trailing data"));
        assert!(err("1 2").contains("trailing data"));
        assert!(err("{}x").contains("trailing data"));
        assert!(err("null\u{000c}").contains("trailing data"), "form feed is not JSON whitespace");
        assert!(err("").contains("unexpected end of JSON"));
        assert!(err("   ").contains("unexpected end of JSON"));
        assert_eq!(parse(" \t\r\n{} \t\r\n").unwrap(), obj(vec![]));
        assert!(err("[1,]").contains("unexpected JSON byte"));
        assert!(err("{\"a\":1,}").contains("object key must be a string"));
        assert!(err("nul").contains("invalid JSON literal"));
    }

    #[test]
    fn duplicate_keys_are_refused_and_named() {
        let message = err("{\"a\":1,\"a\":2}");
        assert!(message.contains("duplicate object key \"a\""), "{message}");
        let message = err("{\"x\":{\"agent_id\":\"a\",\"ttl\":1,\"agent_id\":\"b\"}}");
        assert!(message.contains("duplicate object key \"agent_id\""), "{message}");
        // The same key in two different objects is fine.
        assert!(parse("[{\"a\":1},{\"a\":2}]").is_ok());
        // A wide object switches to a set and still finds the repeat.
        let mut wide: Vec<String> = (0..200).map(|n| format!("\"k{n}\":{n}")).collect();
        assert!(parse(&format!("{{{}}}", wide.join(","))).is_ok());
        wide.push("\"k150\":0".into());
        let message = err(&format!("{{{}}}", wide.join(",")));
        assert!(message.contains("duplicate object key \"k150\""), "{message}");
    }

    #[test]
    fn the_embedded_tool_schema_is_strict_json() {
        let schema = parse(include_str!("../assets/tools.json")).unwrap();
        assert!(schema.get("tools").is_some());
    }
}
