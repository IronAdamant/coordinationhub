//! Fail when Cargo.toml declares a crate dependency.
//!
//! Usage: cargo run --release --example check_zero_deps [-- ROOT]
//!
//! Reads ROOT/Cargo.toml (default: the current directory) the way the
//! Python script read it with tomllib: the top-level `dependencies`,
//! `dev-dependencies` and `build-dependencies` tables must be empty, and
//! there must be no build.rs. Prints `dependency_count 0`, or on stderr
//! `dependency tables must be empty: {...} {...} {...}` (Python dict repr)
//! or `build.rs is not allowed`, with exit 1.
//!
//! Since 1.3.0 it also refuses what the Python script let through: the
//! underscore spellings Cargo accepts (`dev_dependencies`,
//! `build_dependencies`), target-specific tables
//! (`[target.'cfg(unix)'.dependencies]` and the like),
//! `[workspace.dependencies]`, and a `package.build` script. Each one found
//! is printed on its own stderr line as `<table> must be empty: {...}`.

use std::path::PathBuf;

const TABLES: [&str; 3] = ["dependencies", "dev-dependencies", "build-dependencies"];

/// Every name Cargo reads as a dependency table, with its underscore aliases.
const ALL_TABLES: [&str; 5] = [
    "dependencies",
    "dev-dependencies",
    "dev_dependencies",
    "build-dependencies",
    "build_dependencies",
];

/// A TOML value, rendered the way Python prints what tomllib returns.
enum Toml {
    Text(String),
    Raw(String),
    List(Vec<Toml>),
    Table(Vec<(String, Toml)>),
}

impl Toml {
    fn repr(&self) -> String {
        match self {
            Toml::Text(text) => coordinationhub::argparse::py_repr(text),
            Toml::Raw(raw) => raw.clone(),
            Toml::List(items) => format!("[{}]", items.iter().map(Toml::repr).collect::<Vec<_>>().join(", ")),
            Toml::Table(pairs) => format!(
                "{{{}}}",
                pairs
                    .iter()
                    .map(|(key, value)| format!("{}: {}", coordinationhub::argparse::py_repr(key), value.repr()))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        }
    }
}

struct Reader<'a> {
    text: &'a [u8],
    at: usize,
}

impl Reader<'_> {
    fn skip_space(&mut self) {
        while self.at < self.text.len() && matches!(self.text[self.at], b' ' | b'\t') {
            self.at += 1;
        }
    }

    fn key(&mut self) -> String {
        self.skip_space();
        if self.at < self.text.len() && matches!(self.text[self.at], b'"' | b'\'') {
            if let Toml::Text(text) = self.value() {
                return text;
            }
        }
        let start = self.at;
        while self.at < self.text.len()
            && (self.text[self.at].is_ascii_alphanumeric() || matches!(self.text[self.at], b'_' | b'-'))
        {
            self.at += 1;
        }
        String::from_utf8_lossy(&self.text[start..self.at]).into_owned()
    }

    /// Dotted key: `a.b."c"`.
    fn dotted(&mut self) -> Vec<String> {
        let mut parts = vec![self.key()];
        loop {
            self.skip_space();
            if self.at < self.text.len() && self.text[self.at] == b'.' {
                self.at += 1;
                parts.push(self.key());
            } else {
                return parts;
            }
        }
    }

    fn value(&mut self) -> Toml {
        self.skip_space();
        let Some(&first) = self.text.get(self.at) else {
            return Toml::Raw(String::new());
        };
        match first {
            b'"' | b'\'' => {
                self.at += 1;
                let mut out = String::new();
                while self.at < self.text.len() && self.text[self.at] != first {
                    if first == b'"' && self.text[self.at] == b'\\' && self.at + 1 < self.text.len() {
                        self.at += 1;
                        out.push(match self.text[self.at] {
                            b'n' => '\n',
                            b't' => '\t',
                            other => other as char,
                        });
                    } else {
                        let ch_len = std::str::from_utf8(&self.text[self.at..])
                            .ok()
                            .and_then(|rest| rest.chars().next())
                            .map(char::len_utf8)
                            .unwrap_or(1);
                        out.push_str(&String::from_utf8_lossy(&self.text[self.at..self.at + ch_len]));
                        self.at += ch_len - 1;
                    }
                    self.at += 1;
                }
                self.at += 1;
                Toml::Text(out)
            }
            b'[' => {
                self.at += 1;
                let mut items = Vec::new();
                loop {
                    self.skip_blank();
                    if self.at >= self.text.len() || self.text[self.at] == b']' {
                        self.at += 1;
                        return Toml::List(items);
                    }
                    items.push(self.value());
                    self.skip_blank();
                    if self.at < self.text.len() && self.text[self.at] == b',' {
                        self.at += 1;
                    }
                }
            }
            b'{' => {
                self.at += 1;
                let mut table: Vec<(String, Toml)> = Vec::new();
                loop {
                    self.skip_space();
                    if self.at >= self.text.len() || self.text[self.at] == b'}' {
                        self.at += 1;
                        return Toml::Table(table);
                    }
                    let key = self.dotted();
                    self.skip_space();
                    self.at += 1; // '='
                    let value = self.value();
                    insert(&mut table, &key, value);
                    self.skip_space();
                    if self.at < self.text.len() && self.text[self.at] == b',' {
                        self.at += 1;
                    }
                }
            }
            _ => {
                let start = self.at;
                while self.at < self.text.len() && !matches!(self.text[self.at], b',' | b'}' | b']' | b'\n' | b'#') {
                    self.at += 1;
                }
                let raw = String::from_utf8_lossy(&self.text[start..self.at]).trim().to_string();
                Toml::Raw(match raw.as_str() {
                    "true" => "True".into(),
                    "false" => "False".into(),
                    other => other.replace('_', ""),
                })
            }
        }
    }

    fn skip_blank(&mut self) {
        while self.at < self.text.len() {
            match self.text[self.at] {
                b' ' | b'\t' | b'\r' | b'\n' => self.at += 1,
                b'#' => {
                    while self.at < self.text.len() && self.text[self.at] != b'\n' {
                        self.at += 1;
                    }
                }
                _ => break,
            }
        }
    }
}

fn insert(table: &mut Vec<(String, Toml)>, key: &[String], value: Toml) {
    let Some((first, rest)) = key.split_first() else {
        return;
    };
    if rest.is_empty() {
        table.push((first.clone(), value));
        return;
    }
    if !table.iter().any(|(name, _)| name == first) {
        table.push((first.clone(), Toml::Table(Vec::new())));
    }
    if let Some((_, Toml::Table(inner))) = table.iter_mut().find(|(name, _)| name == first) {
        insert(inner, rest, value);
    }
}

/// The three top-level dependency tables of `text`, as tomllib would build
/// them.
fn dependency_tables(text: &str) -> [Vec<(String, Toml)>; 3] {
    let mut root = parse(text);
    TABLES.map(|name| match root.iter().position(|(key, _)| key == name) {
        Some(at) => match root.remove(at).1 {
            Toml::Table(pairs) => pairs,
            other => vec![(String::new(), other)],
        },
        None => Vec::new(),
    })
}

/// Every other place a dependency can be declared, each as
/// `(where, what it holds)`: the underscore aliases, target-specific
/// tables, `workspace.dependencies` and `package.build`.
fn other_dependencies(text: &str) -> Vec<(String, String)> {
    let root = parse(text);
    let mut found = Vec::new();
    let non_empty = |value: &Toml| !matches!(value, Toml::Table(pairs) if pairs.is_empty());
    for name in ["dev_dependencies", "build_dependencies"] {
        if let Some((_, value)) = root.iter().find(|(key, _)| key == name) {
            if non_empty(value) {
                found.push((name.to_string(), value.repr()));
            }
        }
    }
    if let Some((_, Toml::Table(targets))) = root.iter().find(|(key, _)| key == "target") {
        for (cfg, body) in targets {
            let Toml::Table(tables) = body else {
                continue;
            };
            for (name, value) in tables {
                if ALL_TABLES.contains(&name.as_str()) && non_empty(value) {
                    found.push((format!("target.{}.{name}", quote_key(cfg)), value.repr()));
                }
            }
        }
    }
    if let Some((_, Toml::Table(workspace))) = root.iter().find(|(key, _)| key == "workspace") {
        if let Some((_, value)) = workspace.iter().find(|(key, _)| key == "dependencies") {
            if non_empty(value) {
                found.push(("workspace.dependencies".to_string(), value.repr()));
            }
        }
    }
    if let Some((_, Toml::Table(package))) = root.iter().find(|(key, _)| key == "package") {
        if let Some((_, value)) = package.iter().find(|(key, _)| key == "build") {
            if !matches!(value, Toml::Raw(raw) if raw == "False") {
                found.push(("package.build".to_string(), value.repr()));
            }
        }
    }
    found
}

/// A table key as Cargo.toml would spell it in a header.
fn quote_key(key: &str) -> String {
    if !key.is_empty() && key.chars().all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-') {
        key.to_string()
    } else {
        format!("'{key}'")
    }
}

/// The whole document, as tomllib would build it.
fn parse(text: &str) -> Vec<(String, Toml)> {
    let mut root: Vec<(String, Toml)> = Vec::new();
    let mut current: Vec<String> = Vec::new();
    let mut reader = Reader { text: text.as_bytes(), at: 0 };
    loop {
        reader.skip_blank();
        if reader.at >= reader.text.len() {
            break;
        }
        if reader.text[reader.at] == b'[' {
            let array = reader.text.get(reader.at + 1) == Some(&b'[');
            reader.at += if array { 2 } else { 1 };
            current = reader.dotted();
            while reader.at < reader.text.len() && reader.text[reader.at] != b'\n' {
                reader.at += 1;
            }
            if !array {
                ensure(&mut root, &current);
            }
            continue;
        }
        let key = reader.dotted();
        reader.skip_space();
        reader.at += 1; // '='
        let value = reader.value();
        let mut full = current.clone();
        full.extend(key);
        insert(&mut root, &full, value);
    }
    root
}

fn ensure(table: &mut Vec<(String, Toml)>, key: &[String]) {
    let Some((first, rest)) = key.split_first() else {
        return;
    };
    if !table.iter().any(|(name, _)| name == first) {
        table.push((first.clone(), Toml::Table(Vec::new())));
    }
    if let Some((_, Toml::Table(inner))) = table.iter_mut().find(|(name, _)| name == first) {
        ensure(inner, rest);
    }
}

/// The stderr lines for every dependency `text` declares; empty when it
/// declares none.
pub fn problems(text: &str) -> Vec<String> {
    let mut lines = Vec::new();
    let [deps, dev, build] = dependency_tables(text);
    if !deps.is_empty() || !dev.is_empty() || !build.is_empty() {
        lines.push(format!(
            "dependency tables must be empty: {} {} {}",
            Toml::Table(deps).repr(),
            Toml::Table(dev).repr(),
            Toml::Table(build).repr()
        ));
    }
    for (place, held) in other_dependencies(text) {
        lines.push(format!("{place} must be empty: {held}"));
    }
    lines
}

fn main() {
    let root = std::env::args().nth(1).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
    let text = match std::fs::read_to_string(root.join("Cargo.toml")) {
        Ok(text) => text,
        Err(err) => {
            eprintln!("cannot read Cargo.toml: {err}");
            std::process::exit(1);
        }
    };
    let found = problems(&text);
    for line in &found {
        eprintln!("{line}");
    }
    if !found.is_empty() {
        std::process::exit(1);
    }
    if root.join("build.rs").exists() {
        eprintln!("build.rs is not allowed");
        std::process::exit(1);
    }
    println!("dependency_count 0");
}
