//! Minimal JSON value, parser, and writer. Object keys keep insertion order.

use std::collections::BTreeMap;
use std::fmt::Write as _;

#[derive(Clone, Debug, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    String(String),
    Array(Vec<Json>),
    Object(Vec<(String, Json)>),
}

impl Json {
    pub fn object(pairs: Vec<(&str, Json)>) -> Json {
        Json::Object(
            pairs
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect(),
        )
    }

    pub fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Object(pairs) => pairs.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Json::String(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Json::Bool(b) => Some(*b),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Json::Int(n) => Some(*n),
            Json::Float(f) if f.fract() == 0.0 && *f >= i64::MIN as f64 && *f <= i64::MAX as f64 => {
                Some(*f as i64)
            }
            _ => None,
        }
    }

    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Json::Int(n) => Some(*n as f64),
            Json::Float(f) => Some(*f),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&[Json]> {
        match self {
            Json::Array(a) => Some(a),
            _ => None,
        }
    }

    pub fn is_null(&self) -> bool {
        matches!(self, Json::Null)
    }

    pub fn ty_name(&self) -> &'static str {
        match self {
            Json::Null => "NoneType",
            Json::Bool(_) => "bool",
            Json::Int(_) => "int",
            Json::Float(_) => "float",
            Json::String(_) => "str",
            Json::Array(_) => "list",
            Json::Object(_) => "dict",
        }
    }

    pub fn dump(&self) -> String {
        let mut out = String::new();
        self.write_into(&mut out, None, 0);
        out
    }

    pub fn dump_pretty(&self) -> String {
        let mut out = String::new();
        self.write_into(&mut out, Some(2), 0);
        out.push('\n');
        out
    }

    fn write_into(&self, out: &mut String, indent: Option<usize>, level: usize) {
        match self {
            Json::Null => out.push_str("null"),
            Json::Bool(true) => out.push_str("true"),
            Json::Bool(false) => out.push_str("false"),
            Json::Int(n) => {
                let _ = write!(out, "{n}");
            }
            Json::Float(n) => write_float(out, *n),
            Json::String(s) => write_string(out, s),
            Json::Array(items) => {
                if items.is_empty() {
                    out.push_str("[]");
                    return;
                }
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    if let Some(width) = indent {
                        out.push('\n');
                        pad(out, width * (level + 1));
                    } else {
                        out.push(' ');
                    }
                    item.write_into(out, indent, level + 1);
                }
                if let Some(width) = indent {
                    out.push('\n');
                    pad(out, width * level);
                }
                out.push(']');
            }
            Json::Object(pairs) => {
                if pairs.is_empty() {
                    out.push_str("{}");
                    return;
                }
                out.push('{');
                for (i, (k, v)) in pairs.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    if let Some(width) = indent {
                        out.push('\n');
                        pad(out, width * (level + 1));
                    } else {
                        out.push(' ');
                    }
                    write_string(out, k);
                    out.push_str(": ");
                    v.write_into(out, indent, level + 1);
                }
                if indent.is_some() {
                    out.push('\n');
                    if let Some(width) = indent {
                        pad(out, width * level);
                    }
                } else {
                    out.push(' ');
                }
                out.push('}');
            }
        }
    }
}

fn pad(out: &mut String, n: usize) {
    for _ in 0..n {
        out.push(' ');
    }
}

fn write_float(out: &mut String, n: f64) {
    if n.is_nan() {
        out.push_str("NaN");
        return;
    }
    if n.is_infinite() {
        out.push_str(if n.is_sign_negative() {
            "-Infinity"
        } else {
            "Infinity"
        });
        return;
    }
    if n == 0.0 {
        out.push_str(if n.is_sign_negative() { "-0.0" } else { "0.0" });
        return;
    }
    if n.fract() == 0.0 && n.abs() < 1e16 {
        let _ = write!(out, "{n:.1}");
        return;
    }
    let mut s = format!("{n:.16}");
    if let Some(dot) = s.find('.') {
        let trimmed = s[dot + 1..].trim_end_matches('0');
        if trimmed.is_empty() {
            s.truncate(dot + 2);
            if s.ends_with('.') {
                s.push('0');
            }
        } else {
            s.truncate(dot + 1 + trimmed.len());
        }
    }
    out.push_str(&s);
}

fn write_string(out: &mut String, s: &str) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

pub fn parse(input: &str) -> Result<Json, String> {
    let mut p = Parser {
        bytes: input.as_bytes(),
        i: 0,
    };
    p.skip();
    let v = p.parse_value()?;
    p.skip();
    if p.i != p.bytes.len() {
        return Err("trailing data".into());
    }
    Ok(v)
}

struct Parser<'a> {
    bytes: &'a [u8],
    i: usize,
}

impl<'a> Parser<'a> {
    fn skip(&mut self) {
        while let Some(b) = self.bytes.get(self.i) {
            if b.is_ascii_whitespace() {
                self.i += 1;
            } else {
                break;
            }
        }
    }

    fn parse_value(&mut self) -> Result<Json, String> {
        self.skip();
        let b = *self.bytes.get(self.i).ok_or("unexpected end")?;
        match b {
            b'n' => self.consume_ident("null").map(|_| Json::Null),
            b't' => self.consume_ident("true").map(|_| Json::Bool(true)),
            b'f' => self.consume_ident("false").map(|_| Json::Bool(false)),
            b'"' => self.parse_string().map(Json::String),
            b'[' => self.parse_array(),
            b'{' => self.parse_object(),
            b'-' | b'0'..=b'9' => self.parse_number(),
            _ => Err(format!("unexpected byte {b}")),
        }
    }

    fn consume_ident(&mut self, s: &str) -> Result<(), String> {
        if self.bytes[self.i..].starts_with(s.as_bytes()) {
            self.i += s.len();
            Ok(())
        } else {
            Err(format!("expected {s}"))
        }
    }

    fn parse_array(&mut self) -> Result<Json, String> {
        self.i += 1;
        let mut items = Vec::new();
        self.skip();
        if self.bytes.get(self.i) == Some(&b']') {
            self.i += 1;
            return Ok(Json::Array(items));
        }
        loop {
            items.push(self.parse_value()?);
            self.skip();
            match self.bytes.get(self.i) {
                Some(&b',') => {
                    self.i += 1;
                }
                Some(&b']') => {
                    self.i += 1;
                    break;
                }
                _ => return Err("expected ',' or ']'".into()),
            }
        }
        Ok(Json::Array(items))
    }

    fn parse_object(&mut self) -> Result<Json, String> {
        self.i += 1;
        let mut pairs = Vec::new();
        self.skip();
        if self.bytes.get(self.i) == Some(&b'}') {
            self.i += 1;
            return Ok(Json::Object(pairs));
        }
        loop {
            self.skip();
            if self.bytes.get(self.i) != Some(&b'"') {
                return Err("expected string key".into());
            }
            let key = self.parse_string()?;
            self.skip();
            if self.bytes.get(self.i) != Some(&b':') {
                return Err("expected ':'".into());
            }
            self.i += 1;
            let val = self.parse_value()?;
            pairs.push((key, val));
            self.skip();
            match self.bytes.get(self.i) {
                Some(&b',') => self.i += 1,
                Some(&b'}') => {
                    self.i += 1;
                    break;
                }
                _ => return Err("expected ',' or '}'".into()),
            }
        }
        Ok(Json::Object(pairs))
    }

    fn parse_string(&mut self) -> Result<String, String> {
        self.i += 1;
        let mut s = String::new();
        loop {
            let b = *self.bytes.get(self.i).ok_or("unterminated string")?;
            if b == b'"' {
                self.i += 1;
                return Ok(s);
            }
            if b == b'\\' {
                self.i += 1;
                let e = *self.bytes.get(self.i).ok_or("bad escape")?;
                self.i += 1;
                match e {
                    b'"' => s.push('"'),
                    b'\\' => s.push('\\'),
                    b'/' => s.push('/'),
                    b'b' => s.push('\u{08}'),
                    b'f' => s.push('\u{0c}'),
                    b'n' => s.push('\n'),
                    b'r' => s.push('\r'),
                    b't' => s.push('\t'),
                    b'u' => {
                        let hex = self.bytes.get(self.i..self.i + 4).ok_or("bad \\u")?;
                        self.i += 4;
                        let text = std::str::from_utf8(hex).map_err(|_| "bad \\u")?;
                        let cp = u32::from_str_radix(text, 16).map_err(|_| "bad \\u")?;
                        s.push(char::from_u32(cp).ok_or("bad \\u")?);
                    }
                    _ => return Err("bad escape".into()),
                }
                continue;
            }
            let width = match b {
                0x00..=0x7F => 1,
                0xC0..=0xDF => 2,
                0xE0..=0xEF => 3,
                0xF0..=0xF7 => 4,
                _ => return Err("bad utf8".into()),
            };
            let chunk = self.bytes.get(self.i..self.i + width).ok_or("bad utf8")?;
            let text = std::str::from_utf8(chunk).map_err(|_| "bad utf8")?;
            s.push_str(text);
            self.i += width;
        }
    }

    fn parse_number(&mut self) -> Result<Json, String> {
        let start = self.i;
        if self.bytes.get(self.i) == Some(&b'-') {
            self.i += 1;
        }
        while matches!(self.bytes.get(self.i), Some(b'0'..=b'9')) {
            self.i += 1;
        }
        let mut is_float = false;
        if self.bytes.get(self.i) == Some(&b'.') {
            is_float = true;
            self.i += 1;
            while matches!(self.bytes.get(self.i), Some(b'0'..=b'9')) {
                self.i += 1;
            }
        }
        if matches!(self.bytes.get(self.i), Some(b'e' | b'E')) {
            is_float = true;
            self.i += 1;
            if matches!(self.bytes.get(self.i), Some(b'+' | b'-')) {
                self.i += 1;
            }
            while matches!(self.bytes.get(self.i), Some(b'0'..=b'9')) {
                self.i += 1;
            }
        }
        let text = std::str::from_utf8(&self.bytes[start..self.i]).map_err(|_| "bad number")?;
        if is_float {
            let f: f64 = text.parse().map_err(|_| "bad number")?;
            Ok(Json::Float(f))
        } else {
            let n: i64 = text.parse().map_err(|_| "bad number")?;
            Ok(Json::Int(n))
        }
    }
}

/// Stable key order for canonical comparisons inside tests.
pub fn sort_keys(v: &Json) -> Json {
    match v {
        Json::Array(a) => Json::Array(a.iter().map(sort_keys).collect()),
        Json::Object(pairs) => {
            let mut map = BTreeMap::new();
            for (k, val) in pairs {
                map.insert(k.clone(), sort_keys(val));
            }
            Json::Object(map.into_iter().collect())
        }
        other => other.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_object() {
        let v = parse(r#"{"b": 1, "a": [true, null, "x"], "f": 1.5}"#).unwrap();
        let again = parse(&v.dump()).unwrap();
        assert_eq!(v, again);
    }
}
