//! CPython 3.12's `json.loads` (the C scanner's acceptance rules and its
//! `JSONDecodeError` messages) and the `json.dumps(obj, indent=2,
//! sort_keys=True)` shape `update_dispatch_floor.py` writes.

use std::collections::BTreeMap;

use coordinationhub::argparse::py_float_repr;

use super::pyio::py_repr;

#[derive(Clone, Debug)]
pub enum J {
    Null,
    Bool(bool),
    /// Canonical decimal text of a Python `int`.
    Int(String),
    Float(f64),
    Str(String),
    Arr(Vec<J>),
    /// Insertion-ordered; a repeated key keeps its first position and its
    /// last value, like a `dict`.
    Obj(Vec<(String, J)>),
}

impl J {
    pub fn get(&self, key: &str) -> Option<&J> {
        match self {
            J::Obj(items) => items.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn type_name(&self) -> &'static str {
        match self {
            J::Null => "NoneType",
            J::Bool(_) => "bool",
            J::Int(_) => "int",
            J::Float(_) => "float",
            J::Str(_) => "str",
            J::Arr(_) => "list",
            J::Obj(_) => "dict",
        }
    }

    /// Python truthiness.
    pub fn truthy(&self) -> bool {
        match self {
            J::Null => false,
            J::Bool(b) => *b,
            J::Int(text) => text != "0",
            J::Float(value) => *value != 0.0,
            J::Str(text) => !text.is_empty(),
            J::Arr(items) => !items.is_empty(),
            J::Obj(items) => !items.is_empty(),
        }
    }

    /// `str(value)`.
    pub fn py_str(&self) -> String {
        match self {
            J::Str(text) => text.clone(),
            other => other.py_repr(),
        }
    }

    /// `repr(value)`.
    pub fn py_repr(&self) -> String {
        match self {
            J::Null => "None".into(),
            J::Bool(true) => "True".into(),
            J::Bool(false) => "False".into(),
            J::Int(text) => text.clone(),
            J::Float(value) => py_float_repr(*value),
            J::Str(text) => py_repr(text),
            J::Arr(items) => format!("[{}]", items.iter().map(J::py_repr).collect::<Vec<_>>().join(", ")),
            J::Obj(items) => format!(
                "{{{}}}",
                items
                    .iter()
                    .map(|(k, v)| format!("{}: {}", py_repr(k), v.py_repr()))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        }
    }
}

/// `json.loads(text)`; `Err` is the `JSONDecodeError` message (its `str`).
pub fn loads(text: &str) -> Result<J, String> {
    let chars: Vec<char> = text.chars().collect();
    let scanner = Scanner { s: &chars };
    if chars.first() == Some(&'\u{feff}') {
        return Err(scanner.msg("Unexpected UTF-8 BOM (decode using utf-8-sig)", 0));
    }
    let idx = scanner.skip_ws(0);
    let (value, end) = match scanner.scan_once(idx) {
        Ok(found) => found,
        Err(Fail::Stop(pos)) => return Err(scanner.msg("Expecting value", pos)),
        Err(Fail::Msg(message)) => return Err(message),
    };
    let end = scanner.skip_ws(end);
    if end != chars.len() {
        return Err(scanner.msg("Extra data", end));
    }
    Ok(value)
}

enum Fail {
    /// `StopIteration(pos)`: no JSON value starts here.
    Stop(usize),
    Msg(String),
}

struct Scanner<'a> {
    s: &'a [char],
}

fn is_ws(ch: char) -> bool {
    matches!(ch, ' ' | '\t' | '\n' | '\r')
}

impl Scanner<'_> {
    fn msg(&self, what: &str, pos: usize) -> String {
        let before = &self.s[..pos.min(self.s.len())];
        let lineno = before.iter().filter(|c| **c == '\n').count() + 1;
        let colno = match before.iter().rposition(|c| *c == '\n') {
            Some(nl) => pos - nl,
            None => pos + 1,
        };
        format!("{what}: line {lineno} column {colno} (char {pos})")
    }

    fn err<T>(&self, what: &str, pos: usize) -> Result<T, Fail> {
        Err(Fail::Msg(self.msg(what, pos)))
    }

    fn skip_ws(&self, mut idx: usize) -> usize {
        while idx < self.s.len() && is_ws(self.s[idx]) {
            idx += 1;
        }
        idx
    }

    fn at(&self, idx: usize, word: &str) -> bool {
        let w: Vec<char> = word.chars().collect();
        self.s.len() >= idx + w.len() && self.s[idx..idx + w.len()] == w[..]
    }

    fn scan_once(&self, idx: usize) -> Result<(J, usize), Fail> {
        let Some(&ch) = self.s.get(idx) else {
            return Err(Fail::Stop(idx));
        };
        match ch {
            '"' => {
                let (text, end) = self.scanstring(idx + 1)?;
                return Ok((J::Str(text), end));
            }
            '{' => return self.parse_object(idx + 1),
            '[' => return self.parse_array(idx + 1),
            'n' if self.at(idx, "null") => return Ok((J::Null, idx + 4)),
            't' if self.at(idx, "true") => return Ok((J::Bool(true), idx + 4)),
            'f' if self.at(idx, "false") => return Ok((J::Bool(false), idx + 5)),
            'N' if self.at(idx, "NaN") => return Ok((J::Float(f64::NAN), idx + 3)),
            'I' if self.at(idx, "Infinity") => return Ok((J::Float(f64::INFINITY), idx + 8)),
            '-' if self.at(idx, "-Infinity") => return Ok((J::Float(f64::NEG_INFINITY), idx + 9)),
            _ => {}
        }
        self.match_number(idx)
    }

    fn match_number(&self, start: usize) -> Result<(J, usize), Fail> {
        let s = self.s;
        let mut idx = start;
        if s.get(idx) == Some(&'-') {
            idx += 1;
        }
        match s.get(idx) {
            Some('1'..='9') => {
                idx += 1;
                while s.get(idx).is_some_and(|c| c.is_ascii_digit()) {
                    idx += 1;
                }
            }
            Some('0') => idx += 1,
            _ => return Err(Fail::Stop(start)),
        }
        let mut is_float = false;
        if s.get(idx) == Some(&'.') && s.get(idx + 1).is_some_and(|c| c.is_ascii_digit()) {
            is_float = true;
            idx += 2;
            while s.get(idx).is_some_and(|c| c.is_ascii_digit()) {
                idx += 1;
            }
        }
        if matches!(s.get(idx), Some('e' | 'E')) {
            let e_start = idx;
            idx += 1;
            if matches!(s.get(idx), Some('-' | '+')) {
                idx += 1;
            }
            let digits_start = idx;
            while s.get(idx).is_some_and(|c| c.is_ascii_digit()) {
                idx += 1;
            }
            if idx > digits_start {
                is_float = true;
            } else {
                idx = e_start;
            }
        }
        let text: String = s[start..idx].iter().collect();
        if is_float {
            Ok((J::Float(text.parse::<f64>().unwrap_or(f64::NAN)), idx))
        } else {
            let (negative, digits) = match text.strip_prefix('-') {
                Some(rest) => (true, rest),
                None => (false, text.as_str()),
            };
            let trimmed = digits.trim_start_matches('0');
            let canonical = if trimmed.is_empty() {
                "0".to_string()
            } else if negative {
                format!("-{trimmed}")
            } else {
                trimmed.to_string()
            };
            Ok((J::Int(canonical), idx))
        }
    }

    fn scanstring(&self, mut end: usize) -> Result<(String, usize), Fail> {
        let s = self.s;
        let begin = end - 1;
        let mut out = String::new();
        loop {
            let mut next = end;
            let mut c = '\0';
            while next < s.len() {
                c = s[next];
                if c == '"' || c == '\\' {
                    break;
                }
                if (c as u32) <= 0x1f {
                    return self.err("Invalid control character at", next);
                }
                next += 1;
            }
            if next >= s.len() || (c != '"' && c != '\\') {
                return self.err("Unterminated string starting at", begin);
            }
            out.extend(&s[end..next]);
            next += 1;
            if c == '"' {
                end = next;
                break;
            }
            if next == s.len() {
                return self.err("Unterminated string starting at", begin);
            }
            let esc = s[next];
            if esc != 'u' {
                end = next + 1;
                let decoded = match esc {
                    '"' => '"',
                    '\\' => '\\',
                    '/' => '/',
                    'b' => '\x08',
                    'f' => '\x0c',
                    'n' => '\n',
                    'r' => '\r',
                    't' => '\t',
                    _ => return self.err("Invalid \\escape", end - 2),
                };
                out.push(decoded);
            } else {
                next += 1;
                end = next + 4;
                if end >= s.len() {
                    return self.err("Invalid \\uXXXX escape", next - 1);
                }
                let mut code = match hex4(&s[next..end]) {
                    Some(v) => v,
                    None => return self.err("Invalid \\uXXXX escape", end - 5),
                };
                if (0xD800..=0xDBFF).contains(&code) && end + 6 < s.len() && s[end] == '\\' && s[end + 1] == 'u' {
                    let low_start = end + 2;
                    let low_end = low_start + 4;
                    let low = match hex4(&s[low_start..low_end]) {
                        Some(v) => v,
                        None => return self.err("Invalid \\uXXXX escape", low_end - 5),
                    };
                    if (0xDC00..=0xDFFF).contains(&low) {
                        code = 0x10000 + (((code - 0xD800) << 10) | (low - 0xDC00));
                        end = low_end;
                    }
                }
                out.push(char::from_u32(code).unwrap_or('\u{fffd}'));
            }
        }
        Ok((out, end))
    }

    fn parse_object(&self, start: usize) -> Result<(J, usize), Fail> {
        let s = self.s;
        let mut items: Vec<(String, J)> = Vec::new();
        let mut idx = self.skip_ws(start);
        if idx >= s.len() || s[idx] != '}' {
            loop {
                if idx >= s.len() || s[idx] != '"' {
                    return self.err("Expecting property name enclosed in double quotes", idx);
                }
                let (key, next) = self.scanstring(idx + 1)?;
                idx = self.skip_ws(next);
                if idx >= s.len() || s[idx] != ':' {
                    return self.err("Expecting ':' delimiter", idx);
                }
                idx = self.skip_ws(idx + 1);
                let (value, next) = self.scan_once(idx)?;
                match items.iter_mut().find(|(k, _)| *k == key) {
                    Some(slot) => slot.1 = value,
                    None => items.push((key, value)),
                }
                idx = self.skip_ws(next);
                if idx < s.len() && s[idx] == '}' {
                    break;
                }
                if idx >= s.len() || s[idx] != ',' {
                    return self.err("Expecting ',' delimiter", idx);
                }
                idx = self.skip_ws(idx + 1);
            }
        }
        Ok((J::Obj(items), idx + 1))
    }

    fn parse_array(&self, start: usize) -> Result<(J, usize), Fail> {
        let s = self.s;
        let mut items = Vec::new();
        let mut idx = self.skip_ws(start);
        if idx >= s.len() || s[idx] != ']' {
            loop {
                let (value, next) = self.scan_once(idx)?;
                items.push(value);
                idx = self.skip_ws(next);
                if idx < s.len() && s[idx] == ']' {
                    break;
                }
                if idx >= s.len() || s[idx] != ',' {
                    return self.err("Expecting ',' delimiter", idx);
                }
                idx = self.skip_ws(idx + 1);
            }
        }
        Ok((J::Arr(items), idx + 1))
    }
}

fn hex4(digits: &[char]) -> Option<u32> {
    if digits.len() != 4 || !digits.iter().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    u32::from_str_radix(&digits.iter().collect::<String>(), 16).ok()
}

/// `json.dumps(s)` for a `str` with the default `ensure_ascii=True`.
pub fn dump_str(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\x08' => out.push_str("\\b"),
            '\x0c' => out.push_str("\\f"),
            ' '..='~' => out.push(ch),
            _ => {
                let mut units = [0u16; 2];
                for unit in ch.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{:04x}", unit));
                }
            }
        }
    }
    out.push('"');
    out
}

/// `json.dumps(mapping, indent=2, sort_keys=True)` for `{str: [str, ...]}`.
pub fn dumps_str_lists(mapping: &BTreeMap<String, Vec<String>>) -> String {
    if mapping.is_empty() {
        return "{}".into();
    }
    let mut out = String::from("{\n");
    let count = mapping.len();
    for (index, (key, values)) in mapping.iter().enumerate() {
        out.push_str("  ");
        out.push_str(&dump_str(key));
        out.push_str(": ");
        if values.is_empty() {
            out.push_str("[]");
        } else {
            out.push_str("[\n");
            for (vi, value) in values.iter().enumerate() {
                out.push_str("    ");
                out.push_str(&dump_str(value));
                if vi + 1 < values.len() {
                    out.push(',');
                }
                out.push('\n');
            }
            out.push_str("  ]");
        }
        if index + 1 < count {
            out.push(',');
        }
        out.push('\n');
    }
    out.push('}');
    out
}
