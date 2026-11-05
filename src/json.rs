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

pub fn parse(input: &str) -> Result<Value, String> {
    let mut parser = Parser {
        bytes: input.as_bytes(),
        index: 0,
    };
    parser.skip();
    let value = parser.parse_value()?;
    parser.skip();
    if parser.index != parser.bytes.len() {
        return Err("trailing data after JSON value".into());
    }
    Ok(value)
}

struct Parser<'a> {
    bytes: &'a [u8],
    index: usize,
}

impl<'a> Parser<'a> {
    fn skip(&mut self) {
        while let Some(c) = self.bytes.get(self.index) {
            if c.is_ascii_whitespace() {
                self.index += 1;
            } else {
                break;
            }
        }
    }

    fn parse_value(&mut self) -> Result<Value, String> {
        self.skip();
        let byte = *self.bytes.get(self.index).ok_or("unexpected end of JSON")?;
        match byte {
            b'n' => self.consume_lit(b"null", Value::Null),
            b't' => self.consume_lit(b"true", Value::Bool(true)),
            b'f' => self.consume_lit(b"false", Value::Bool(false)),
            b'"' => Ok(Value::String(self.parse_string()?)),
            b'[' => self.parse_array(),
            b'{' => self.parse_object(),
            b'-' | b'0'..=b'9' => self.parse_number(),
            _ => Err(format!("unexpected JSON byte {}", byte as char)),
        }
    }

    fn consume_lit(&mut self, lit: &[u8], value: Value) -> Result<Value, String> {
        if self.bytes[self.index..].starts_with(lit) {
            self.index += lit.len();
            Ok(value)
        } else {
            Err("invalid JSON literal".into())
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
                _ => return Err("expected ',' or ']' in array".into()),
            }
        }
        Ok(Value::Array(items))
    }

    fn parse_object(&mut self) -> Result<Value, String> {
        self.index += 1;
        let mut pairs = Vec::new();
        self.skip();
        if self.bytes.get(self.index) == Some(&b'}') {
            self.index += 1;
            return Ok(Value::Object(pairs));
        }
        loop {
            self.skip();
            if self.bytes.get(self.index) != Some(&b'"') {
                return Err("object key must be a string".into());
            }
            let key = self.parse_string()?;
            self.skip();
            if self.bytes.get(self.index) != Some(&b':') {
                return Err("expected ':' after object key".into());
            }
            self.index += 1;
            let value = self.parse_value()?;
            pairs.push((key, value));
            self.skip();
            match self.bytes.get(self.index) {
                Some(&b',') => self.index += 1,
                Some(&b'}') => {
                    self.index += 1;
                    break;
                }
                _ => return Err("expected ',' or '}' in object".into()),
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
                    let esc = *self.bytes.get(self.index).ok_or("bad string escape")?;
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
                                    return Err("invalid unicode scalar".into());
                                }
                            }
                            out.push(char::from_u32(code).ok_or("invalid unicode scalar")?);
                        }
                        _ => return Err("unknown string escape".into()),
                    }
                }
                byte if byte < 0x80 => out.push(byte as char),
                byte => {
                    let width = match byte {
                        0xC2..=0xDF => 2,
                        0xE0..=0xEF => 3,
                        0xF0..=0xF4 => 4,
                        _ => return Err("invalid utf-8 in string".into()),
                    };
                    let start = self.index - 1;
                    if start + width > self.bytes.len() {
                        return Err("invalid utf-8 in string".into());
                    }
                    let slice = &self.bytes[start..start + width];
                    let text = std::str::from_utf8(slice)
                        .map_err(|_| "invalid utf-8 in string".to_string())?;
                    out.push_str(text);
                    self.index = start + width;
                }
            }
        }
        Err("unterminated string".into())
    }

    fn read_hex4(&mut self) -> Result<u32, String> {
        if self.index + 4 > self.bytes.len() {
            return Err("short unicode escape".into());
        }
        let hex = std::str::from_utf8(&self.bytes[self.index..self.index + 4])
            .map_err(|_| "bad unicode escape".to_string())?;
        let code = u32::from_str_radix(hex, 16).map_err(|_| "bad unicode escape".to_string())?;
        self.index += 4;
        Ok(code)
    }

    fn parse_number(&mut self) -> Result<Value, String> {
        let start = self.index;
        if self.bytes.get(self.index) == Some(&b'-') {
            self.index += 1;
        }
        while matches!(self.bytes.get(self.index), Some(b'0'..=b'9')) {
            self.index += 1;
        }
        let mut is_float = false;
        if self.bytes.get(self.index) == Some(&b'.') {
            is_float = true;
            self.index += 1;
            while matches!(self.bytes.get(self.index), Some(b'0'..=b'9')) {
                self.index += 1;
            }
        }
        if matches!(self.bytes.get(self.index), Some(b'e' | b'E')) {
            is_float = true;
            self.index += 1;
            if matches!(self.bytes.get(self.index), Some(b'+' | b'-')) {
                self.index += 1;
            }
            while matches!(self.bytes.get(self.index), Some(b'0'..=b'9')) {
                self.index += 1;
            }
        }
        let text = std::str::from_utf8(&self.bytes[start..self.index])
            .map_err(|_| "bad number".to_string())?;
        if is_float {
            let n: f64 = text.parse().map_err(|_| "bad float".to_string())?;
            Ok(Value::Float(n))
        } else {
            let n: i64 = text.parse().map_err(|_| "bad integer".to_string())?;
            Ok(Value::Int(n))
        }
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

/// `json.dumps` float text: `float.__repr__`, and `NaN` / `Infinity` the
/// way Python's encoder writes them.
fn write_float(out: &mut String, n: f64) {
    if n.is_nan() {
        out.push_str("NaN");
    } else if n.is_infinite() {
        out.push_str(if n > 0.0 { "Infinity" } else { "-Infinity" });
    } else {
        out.push_str(&crate::argparse::py_float_repr(n));
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
}
