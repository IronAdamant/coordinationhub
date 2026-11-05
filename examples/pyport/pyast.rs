//! The `ast` questions the ported scripts ask, answered over [`pytok`]'s
//! tokens and blocks:
//!
//! * statement shapes (`Assign`, `AnnAssign`, `Return`, `Expr`, `def`,
//!   `class`, compound headers and their same-line bodies);
//! * `ast.get_docstring(module)` before cleaning;
//! * a module's public top-level `def`/`class`/assigned names;
//! * the keys of a `NAME = {...}` dict display;
//! * a small expression parser (names, literals, displays, attribute /
//!   call / subscript trailers, unary `-`/`+`, binary `|`/`+`/`-`);
//! * a static evaluator for module-level constants, with the import
//!   resolution `from pkg.mod import NAME` performs against the tree
//!   (relative imports included). It evaluates literal data only, which is
//!   what `TOOL_SCHEMAS`, `TOOL_DISPATCH` and `cli._COMMANDS` are; anything
//!   else is reported rather than guessed.

use std::cell::RefCell;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::rc::Rc;

use super::pyio;
use super::pytok::{self, is_keyword, Kind, Stmt, Tok};

// ------------------------------------------------------------------
// Token-slice helpers
// ------------------------------------------------------------------

/// Split at depth-0 occurrences of operator `op`.
pub fn split_top<'a>(toks: &'a [Tok], op: &str) -> Vec<&'a [Tok]> {
    let mut parts = Vec::new();
    let mut start = 0;
    let mut depth = 0i32;
    for (i, tok) in toks.iter().enumerate() {
        if tok.closes() {
            depth -= 1;
        }
        if depth == 0 && tok.is_op(op) {
            parts.push(&toks[start..i]);
            start = i + 1;
        }
        if tok.opens() {
            depth += 1;
        }
    }
    parts.push(&toks[start..]);
    parts
}

/// Index of the bracket closing the one at `open`.
pub fn matching(toks: &[Tok], open: usize) -> Option<usize> {
    let mut depth = 0i32;
    for (i, tok) in toks.iter().enumerate().skip(open) {
        if tok.opens() {
            depth += 1;
        } else if tok.closes() {
            depth -= 1;
            if depth == 0 {
                return Some(i);
            }
        }
    }
    None
}

/// Remove redundant wrapping parentheses: `((x))` -> `x`.
pub fn strip_parens(mut toks: &[Tok]) -> &[Tok] {
    while toks.len() >= 2 && toks[0].is_op("(") && matching(toks, 0) == Some(toks.len() - 1) {
        toks = &toks[1..toks.len() - 1];
    }
    toks
}

/// The value of an `ast.Constant` whose value is a `str`.
pub fn str_constant(toks: &[Tok]) -> Option<String> {
    let inner = strip_parens(toks);
    if inner.is_empty() || !inner.iter().all(|t| t.kind == Kind::Str && !t.bytes) {
        return None;
    }
    Some(inner.iter().map(|t| t.text.as_str()).collect())
}

/// Any `ast.Constant` (str, bytes, number, True/False/None, `...`).
pub fn is_constant(toks: &[Tok]) -> bool {
    let inner = strip_parens(toks);
    if inner.is_empty() {
        return false;
    }
    if inner.iter().all(|t| t.kind == Kind::Str) {
        return true;
    }
    inner.len() == 1
        && (inner[0].kind == Kind::Number
            || inner[0].is_op("...")
            || (inner[0].kind == Kind::Name && matches!(inner[0].text.as_str(), "True" | "False" | "None")))
}

/// An `ast.Name` target: `x` or `(x)`.
pub fn simple_name(toks: &[Tok]) -> Option<&str> {
    let inner = strip_parens(toks);
    (inner.len() == 1 && inner[0].kind == Kind::Name && !is_keyword(&inner[0].text)).then(|| inner[0].text.as_str())
}

// ------------------------------------------------------------------
// Statement shapes
// ------------------------------------------------------------------

pub enum Simple<'a> {
    Assign { targets: Vec<&'a [Tok]>, value: &'a [Tok] },
    AnnAssign { target: &'a [Tok], value: Option<&'a [Tok]> },
    Return(&'a [Tok]),
    ImportFrom(&'a [Tok]),
    Import(&'a [Tok]),
    Expr(&'a [Tok]),
    Other,
}

const AUG_OPS: &[&str] = &["+=", "-=", "*=", "/=", "//=", "%=", "**=", ">>=", "<<=", "&=", "|=", "^=", "@="];

/// Classify one simple statement (a `;`-separated part of a logical line).
pub fn classify_simple(toks: &[Tok]) -> Simple<'_> {
    let Some(first) = toks.first() else { return Simple::Other };
    if first.kind == Kind::Name {
        match first.text.as_str() {
            "return" => return Simple::Return(&toks[1..]),
            "from" => return Simple::ImportFrom(toks),
            "import" => return Simple::Import(toks),
            "None" | "True" | "False" | "await" | "lambda" | "not" | "yield" => {}
            word if is_keyword(word) => return Simple::Other,
            "type" if toks.get(1).is_some_and(|t| t.kind == Kind::Name) => return Simple::Other,
            _ => {}
        }
    }
    let mut eqs = Vec::new();
    let mut colon = None;
    let mut depth = 0i32;
    for (i, tok) in toks.iter().enumerate() {
        if tok.closes() {
            depth -= 1;
        }
        if depth == 0 {
            if tok.is_name("lambda") {
                break;
            }
            if tok.is_op("=") {
                eqs.push(i);
            } else if tok.is_op(":") && eqs.is_empty() && colon.is_none() {
                colon = Some(i);
            } else if tok.kind == Kind::Op && AUG_OPS.contains(&tok.text.as_str()) && eqs.is_empty() {
                return Simple::Other;
            }
        }
        if tok.opens() {
            depth += 1;
        }
    }
    if let Some(c) = colon {
        return Simple::AnnAssign { target: &toks[..c], value: eqs.first().map(|e| &toks[e + 1..]) };
    }
    if eqs.is_empty() {
        return Simple::Expr(toks);
    }
    let mut targets = Vec::new();
    let mut start = 0;
    for e in &eqs {
        targets.push(&toks[start..*e]);
        start = e + 1;
    }
    Simple::Assign { targets, value: &toks[start..] }
}

pub enum Line<'a> {
    Decorator,
    Def { name: &'a str, is_async: bool },
    Class { name: &'a str },
    /// Any other compound statement header (`if`, `elif`, `else`, `for`,
    /// `while`, `with`, `try`, `except`, `finally`, `match`, `case`, ...).
    Compound { keyword: &'a str },
    Simple,
}

const COMPOUND: &[&str] = &["if", "elif", "else", "for", "while", "with", "try", "except", "finally"];

pub fn classify_line(stmt: &Stmt) -> Line<'_> {
    let toks = &stmt.toks;
    let Some(first) = toks.first() else { return Line::Simple };
    if first.is_op("@") {
        return Line::Decorator;
    }
    let word = |i: usize| toks.get(i).filter(|t| t.kind == Kind::Name).map(|t| t.text.as_str());
    match word(0) {
        Some("def") => {
            if let Some(name) = word(1) {
                return Line::Def { name, is_async: false };
            }
        }
        Some("async") if word(1) == Some("def") => {
            if let Some(name) = word(2) {
                return Line::Def { name, is_async: true };
            }
        }
        Some("async") if matches!(word(1), Some("for") | Some("with")) => {
            return Line::Compound { keyword: word(1).unwrap_or("") };
        }
        Some("class") => {
            if let Some(name) = word(1) {
                return Line::Class { name };
            }
        }
        Some(kw) if COMPOUND.contains(&kw) => return Line::Compound { keyword: kw },
        _ => {}
    }
    if toks.last().is_some_and(|t| t.is_op(":")) {
        return Line::Compound { keyword: word(0).unwrap_or("") };
    }
    Line::Simple
}

/// The `:` ending a compound statement's header (lambda colons skipped).
pub fn header_colon(toks: &[Tok]) -> Option<usize> {
    let mut depth = 0i32;
    let mut lambdas = 0;
    for (i, tok) in toks.iter().enumerate() {
        if tok.closes() {
            depth -= 1;
        }
        if depth == 0 {
            if tok.is_name("lambda") {
                lambdas += 1;
            } else if tok.is_op(":") {
                if lambdas > 0 {
                    lambdas -= 1;
                } else {
                    return Some(i);
                }
            }
        }
        if tok.opens() {
            depth += 1;
        }
    }
    None
}

/// The simple statements written on the header line after the `:`.
pub fn inline_body(toks: &[Tok]) -> Vec<&[Tok]> {
    match header_colon(toks) {
        Some(c) if c + 1 < toks.len() => split_top(&toks[c + 1..], ";").into_iter().filter(|p| !p.is_empty()).collect(),
        _ => Vec::new(),
    }
}

/// The simple statements of a simple logical line.
pub fn simple_parts(stmt: &Stmt) -> Vec<&[Tok]> {
    split_top(&stmt.toks, ";").into_iter().filter(|p| !p.is_empty()).collect()
}

// ------------------------------------------------------------------
// Module-level questions
// ------------------------------------------------------------------

/// `ast.parse(source)`: the block structure, writing to stderr the
/// `SyntaxWarning`s CPython 3.12 prints while parsing (invalid escapes,
/// reported at the literal's first line, for file name `<unknown>`).
pub fn ast_parse(source: &str) -> Result<Vec<Stmt>, pytok::SyntaxError> {
    let (result, warnings) = pytok::parse_blocks_warn(source);
    for (line, msg) in warnings {
        pyio::write_err(&format!("<unknown>:{line}: SyntaxWarning: {msg}\n"));
    }
    result
}

/// `ast.get_docstring(ast.parse(source), clean=False)`; `None` when the
/// source does not parse or has no docstring.
pub fn module_docstring(source: &str) -> Option<String> {
    let stmts = ast_parse(source).ok()?;
    let first = stmts.first()?;
    if !matches!(classify_line(first), Line::Simple) {
        return None;
    }
    let parts = split_top(&first.toks, ";");
    match classify_simple(parts[0]) {
        Simple::Expr(toks) => str_constant(toks),
        _ => None,
    }
}

/// Public `def`/`async def`/`class` names and `Name` assignment targets in
/// a module's body, in order (`propose_stale_phrases.py`). Unparseable
/// source yields nothing, as the script's `SyntaxError` branch does.
pub fn top_level_names(source: &str) -> Vec<String> {
    let Ok(stmts) = ast_parse(source) else { return Vec::new() };
    let mut names = Vec::new();
    for stmt in &stmts {
        match classify_line(stmt) {
            Line::Def { name, .. } | Line::Class { name } => {
                if !name.starts_with('_') {
                    names.push(name.to_string());
                }
            }
            Line::Simple => {
                for part in simple_parts(stmt) {
                    if let Simple::Assign { targets, .. } = classify_simple(part) {
                        for target in targets {
                            if let Some(name) = simple_name(target) {
                                if !name.starts_with('_') {
                                    names.push(name.to_string());
                                }
                            }
                        }
                    }
                }
            }
            _ => {}
        }
    }
    names
}

/// For the first module-level `NAME = {...}` / `NAME: T = {...}` whose
/// value is a dict display, its `str`-constant keys (`None` for `**x`
/// and non-`str` keys). `Ok(None)` if there is no such statement.
pub fn dict_display_keys(stmts: &[Stmt], name: &str) -> Option<Vec<Option<String>>> {
    for stmt in stmts {
        if !matches!(classify_line(stmt), Line::Simple) {
            continue;
        }
        for part in simple_parts(stmt) {
            let (target, value) = match classify_simple(part) {
                Simple::AnnAssign { target, value: Some(value) } => (target, value),
                Simple::Assign { targets, value } if targets.len() == 1 => (targets[0], value),
                _ => continue,
            };
            if simple_name(target) != Some(name) {
                continue;
            }
            if let Some(items) = dict_items(value) {
                return Some(items.iter().map(|(key, _)| key.and_then(str_constant)).collect());
            }
        }
    }
    None
}

/// The `(key, value)` token slices of a dict display (`None` key for
/// `**mapping`); `None` if `toks` is not a dict display.
pub fn dict_items(toks: &[Tok]) -> Option<Vec<(Option<&[Tok]>, &[Tok])>> {
    let toks = strip_parens(toks);
    if toks.len() < 2 || !toks[0].is_op("{") || matching(toks, 0) != Some(toks.len() - 1) {
        return None;
    }
    let inner = &toks[1..toks.len() - 1];
    let mut depth = 0i32;
    for tok in inner {
        if tok.closes() {
            depth -= 1;
        }
        if depth == 0 && tok.is_name("for") {
            return None;
        }
        if tok.opens() {
            depth += 1;
        }
    }
    let mut items = Vec::new();
    for item in split_top(inner, ",") {
        if item.is_empty() {
            continue;
        }
        if item[0].is_op("**") {
            items.push((None, &item[1..]));
            continue;
        }
        let colon = header_colon(item)?;
        items.push((Some(&item[..colon]), &item[colon + 1..]));
    }
    Some(items)
}

// ------------------------------------------------------------------
// Expressions
// ------------------------------------------------------------------

#[derive(Clone, Debug)]
pub enum Expr {
    Name(String),
    Str(String),
    Bytes,
    FString,
    Num(String),
    Tuple(Vec<Expr>),
    List(Vec<Expr>),
    Dict(Vec<(Option<Expr>, Expr)>),
    Attr(Box<Expr>, String),
    Call(Box<Expr>),
    Subscript(Box<Expr>),
    BinOp(char, Box<Expr>, Box<Expr>),
    Neg(Box<Expr>),
    Pos(Box<Expr>),
}

/// Parse a whole expression; `None` if it uses syntax outside the subset
/// (which, for the scripts' questions, means "not the shape asked about").
pub fn parse_expr(toks: &[Tok]) -> Option<Expr> {
    let mut parser = ExprParser { t: toks, i: 0 };
    let expr = parser.expr_list(None)?;
    (parser.i == toks.len()).then_some(expr)
}

struct ExprParser<'a> {
    t: &'a [Tok],
    i: usize,
}

impl ExprParser<'_> {
    fn peek(&self) -> Option<&Tok> {
        self.t.get(self.i)
    }

    fn peek_op(&self, op: &str) -> bool {
        self.peek().is_some_and(|t| t.is_op(op))
    }

    /// Comma-separated expressions up to `close` (or the end); a tuple if
    /// there was a comma.
    fn expr_list(&mut self, close: Option<&str>) -> Option<Expr> {
        let mut items = Vec::new();
        let mut comma = false;
        loop {
            if self.peek().is_none() || close.is_some_and(|c| self.peek_op(c)) {
                break;
            }
            items.push(self.expr()?);
            if self.peek_op(",") {
                self.i += 1;
                comma = true;
            } else {
                break;
            }
        }
        if !comma && items.len() == 1 {
            return items.pop();
        }
        Some(Expr::Tuple(items))
    }

    fn expr(&mut self) -> Option<Expr> {
        let mut left = self.sum()?;
        while self.peek_op("|") {
            self.i += 1;
            let right = self.sum()?;
            left = Expr::BinOp('|', Box::new(left), Box::new(right));
        }
        Some(left)
    }

    fn sum(&mut self) -> Option<Expr> {
        let mut left = self.unary()?;
        loop {
            let op = if self.peek_op("+") {
                '+'
            } else if self.peek_op("-") {
                '-'
            } else {
                break;
            };
            self.i += 1;
            let right = self.unary()?;
            left = Expr::BinOp(op, Box::new(left), Box::new(right));
        }
        Some(left)
    }

    fn unary(&mut self) -> Option<Expr> {
        if self.peek_op("-") {
            self.i += 1;
            return Some(Expr::Neg(Box::new(self.unary()?)));
        }
        if self.peek_op("+") {
            self.i += 1;
            return Some(Expr::Pos(Box::new(self.unary()?)));
        }
        self.primary()
    }

    fn skip_bracket(&mut self) -> Option<()> {
        let close = matching(self.t, self.i)?;
        self.i = close + 1;
        Some(())
    }

    fn primary(&mut self) -> Option<Expr> {
        let mut expr = self.atom()?;
        loop {
            if self.peek_op(".") {
                let name = self.t.get(self.i + 1).filter(|t| t.kind == Kind::Name)?;
                expr = Expr::Attr(Box::new(expr), name.text.clone());
                self.i += 2;
            } else if self.peek_op("(") {
                self.skip_bracket()?;
                expr = Expr::Call(Box::new(expr));
            } else if self.peek_op("[") {
                self.skip_bracket()?;
                expr = Expr::Subscript(Box::new(expr));
            } else {
                return Some(expr);
            }
        }
    }

    fn atom(&mut self) -> Option<Expr> {
        let tok = self.peek()?.clone();
        match tok.kind {
            Kind::Name => {
                if is_keyword(&tok.text) && !matches!(tok.text.as_str(), "True" | "False" | "None") {
                    return None;
                }
                self.i += 1;
                Some(Expr::Name(tok.text))
            }
            Kind::Number => {
                self.i += 1;
                Some(Expr::Num(tok.text))
            }
            Kind::Str | Kind::FStart => {
                let mut text = String::new();
                let (mut bytes, mut fstring) = (false, false);
                while let Some(t) = self.peek() {
                    match t.kind {
                        Kind::Str => {
                            bytes |= t.bytes;
                            text.push_str(&t.text);
                            self.i += 1;
                        }
                        Kind::FStart => {
                            fstring = true;
                            self.skip_bracket()?;
                        }
                        _ => break,
                    }
                }
                Some(if fstring {
                    Expr::FString
                } else if bytes {
                    Expr::Bytes
                } else {
                    Expr::Str(text)
                })
            }
            Kind::Op if tok.text == "(" => {
                self.i += 1;
                if self.peek_op(")") {
                    self.i += 1;
                    return Some(Expr::Tuple(Vec::new()));
                }
                let inner = self.expr_list(Some(")"))?;
                if !self.peek_op(")") {
                    return None;
                }
                self.i += 1;
                Some(inner)
            }
            Kind::Op if tok.text == "[" => {
                self.i += 1;
                let inner = self.expr_list(Some("]"))?;
                if !self.peek_op("]") {
                    return None;
                }
                self.i += 1;
                Some(match inner {
                    Expr::Tuple(items) => Expr::List(items),
                    single => Expr::List(vec![single]),
                })
            }
            Kind::Op if tok.text == "{" => {
                self.i += 1;
                let mut items = Vec::new();
                loop {
                    if self.peek_op("}") {
                        self.i += 1;
                        return Some(Expr::Dict(items));
                    }
                    if self.peek_op("**") {
                        self.i += 1;
                        let value = self.expr()?;
                        items.push((None, value));
                    } else {
                        let key = self.expr()?;
                        if !self.peek_op(":") {
                            return None;
                        }
                        self.i += 1;
                        let value = self.expr()?;
                        items.push((Some(key), value));
                    }
                    if self.peek_op(",") {
                        self.i += 1;
                    } else if !self.peek_op("}") {
                        return None;
                    }
                }
            }
            _ => None,
        }
    }
}

// ------------------------------------------------------------------
// Static evaluation of module constants
// ------------------------------------------------------------------

#[derive(Clone, Debug)]
pub enum Val {
    None,
    Bool(bool),
    Int(i128),
    Float(f64),
    Str(String),
    Tuple(Vec<Val>),
    List(Vec<Val>),
    Dict(Vec<(Val, Val)>),
    /// A value computed at import time (a call, an attribute, ...) that a
    /// static reading cannot know. Harmless until something uses it, the
    /// way `"maxLength": MAX_INTENT` (an `os.environ` lookup) is never
    /// looked at by `gen_docs`.
    Opaque(String),
}

impl Val {
    pub fn type_name(&self) -> &'static str {
        match self {
            Val::None => "NoneType",
            Val::Bool(_) => "bool",
            Val::Int(_) => "int",
            Val::Float(_) => "float",
            Val::Str(_) => "str",
            Val::Tuple(_) => "tuple",
            Val::List(_) => "list",
            Val::Dict(_) => "dict",
            Val::Opaque(_) => "<runtime value>",
        }
    }

    /// `Err` (a description) if this is an opaque runtime value.
    pub fn known(&self) -> Result<&Val, String> {
        match self {
            Val::Opaque(what) => Err(what.clone()),
            other => Ok(other),
        }
    }

    fn key_eq(&self, other: &Val) -> bool {
        let num = |v: &Val| match v {
            Val::Bool(b) => Some(*b as i128 as f64),
            Val::Int(i) => Some(*i as f64),
            Val::Float(f) => Some(*f),
            _ => None,
        };
        match (self, other) {
            (Val::Str(a), Val::Str(b)) => a == b,
            (Val::None, Val::None) => true,
            (Val::Tuple(a), Val::Tuple(b)) => a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.key_eq(y)),
            _ => match (num(self), num(other)) {
                (Some(a), Some(b)) => a == b,
                _ => false,
            },
        }
    }

    /// `str(value)` for the scalar values the scripts format.
    pub fn py_str(&self) -> String {
        match self {
            Val::None => "None".into(),
            Val::Bool(true) => "True".into(),
            Val::Bool(false) => "False".into(),
            Val::Int(i) => i.to_string(),
            Val::Float(f) => coordinationhub::argparse::py_float_repr(*f),
            Val::Str(s) => s.clone(),
            other => format!("<{}>", other.type_name()),
        }
    }
}

pub fn dict_insert(items: &mut Vec<(Val, Val)>, key: Val, value: Val) {
    match items.iter_mut().find(|(k, _)| k.key_eq(&key)) {
        Some(slot) => slot.1 = value,
        None => items.push((key, value)),
    }
}

/// `dict.get(key)` for a `str` key.
pub fn dict_get<'a>(items: &'a [(Val, Val)], key: &str) -> Option<&'a Val> {
    items.iter().find(|(k, _)| matches!(k, Val::Str(s) if s == key)).map(|(_, v)| v)
}

#[derive(Clone)]
enum Binding {
    Expr(Vec<Tok>),
    Import { level: usize, module: Option<String>, name: String },
    Opaque(&'static str),
}

pub struct ModuleSrc {
    pub name: String,
    /// `None` for a namespace package.
    pub file: Option<PathBuf>,
    pub is_package: bool,
    pub stmts: Vec<Stmt>,
}

impl ModuleSrc {
    fn binding(&self, name: &str) -> Option<Binding> {
        self.binding_upto(name, self.stmts.len())
    }

    /// `from m import *` or a module `__getattr__` (PEP 562): names this
    /// static reading cannot enumerate, so none is reported missing.
    fn has_dynamic_names(&self) -> bool {
        self.stmts.iter().any(|stmt| match classify_line(stmt) {
            Line::Def { name, .. } => name == "__getattr__",
            Line::Simple => simple_parts(stmt).iter().any(|part| {
                matches!(classify_simple(part), Simple::ImportFrom(toks)
                    if parse_import_from(toks).is_some_and(|(_, _, names)| names.iter().any(|(o, _)| o == "*")))
            }),
            _ => false,
        })
    }

    /// The binding of `name` after executing the first `limit` statements.
    fn binding_upto(&self, name: &str, limit: usize) -> Option<Binding> {
        let mut found = None;
        for stmt in &self.stmts[..limit.min(self.stmts.len())] {
            match classify_line(stmt) {
                Line::Def { name: n, .. } | Line::Class { name: n } if n == name => {
                    found = Some(Binding::Opaque("a def/class"));
                }
                Line::Simple => {
                    for part in simple_parts(stmt) {
                        if let Some(b) = part_binding(part, name) {
                            found = Some(b);
                        }
                    }
                }
                Line::Compound { .. } => {
                    if binds_inside(&stmt.body, name) || inline_body(&stmt.toks).iter().any(|p| part_binding(p, name).is_some())
                    {
                        found = Some(Binding::Opaque("a conditional binding"));
                    }
                }
                _ => {}
            }
        }
        found
    }
}

fn binds_inside(stmts: &[Stmt], name: &str) -> bool {
    stmts.iter().any(|stmt| match classify_line(stmt) {
        Line::Def { name: n, .. } | Line::Class { name: n } => n == name,
        Line::Simple => simple_parts(stmt).iter().any(|p| part_binding(p, name).is_some()),
        Line::Compound { .. } => {
            binds_inside(&stmt.body, name) || inline_body(&stmt.toks).iter().any(|p| part_binding(p, name).is_some())
        }
        Line::Decorator => false,
    })
}

fn part_binding(part: &[Tok], name: &str) -> Option<Binding> {
    match classify_simple(part) {
        Simple::Assign { targets, value } => {
            let mut hit = None;
            for target in targets {
                if simple_name(target) == Some(name) {
                    hit = Some(Binding::Expr(value.to_vec()));
                } else if target.iter().any(|t| t.is_name(name)) && strip_parens(target).len() > 1 {
                    if split_top(strip_parens(target), ",").len() > 1 {
                        hit = Some(Binding::Opaque("a tuple-unpacking assignment"));
                    }
                }
            }
            hit
        }
        Simple::AnnAssign { target, value: Some(value) } if simple_name(target) == Some(name) => {
            Some(Binding::Expr(value.to_vec()))
        }
        Simple::ImportFrom(toks) => {
            let (level, module, names) = parse_import_from(toks)?;
            names.into_iter().find(|(_, bound)| bound == name).map(|(orig, _)| Binding::Import {
                level,
                module: module.clone(),
                name: orig,
            })
        }
        Simple::Import(toks) => {
            for clause in split_top(&toks[1..], ",") {
                let words: Vec<&str> = clause.iter().filter(|t| t.kind == Kind::Name).map(|t| t.text.as_str()).collect();
                let bound = match words.iter().position(|w| *w == "as") {
                    Some(at) => words.get(at + 1).copied(),
                    None => words.first().copied(),
                };
                if bound == Some(name) {
                    return Some(Binding::Opaque("a module import"));
                }
            }
            None
        }
        _ => None,
    }
}

/// `from <dots><module> import a [as b], ...` -> (level, module, [(orig, bound)]).
pub fn parse_import_from(toks: &[Tok]) -> Option<(usize, Option<String>, Vec<(String, String)>)> {
    let mut i = 1;
    let mut level = 0;
    while let Some(t) = toks.get(i) {
        if t.is_op(".") {
            level += 1;
        } else if t.is_op("...") {
            level += 3;
        } else {
            break;
        }
        i += 1;
    }
    let mut module = String::new();
    while let Some(t) = toks.get(i) {
        if t.is_name("import") {
            break;
        }
        module.push_str(&t.text);
        i += 1;
    }
    if !toks.get(i)?.is_name("import") {
        return None;
    }
    let rest = strip_parens(&toks[i + 1..]);
    let mut names = Vec::new();
    for clause in split_top(rest, ",") {
        let words: Vec<&str> = clause.iter().map(|t| t.text.as_str()).collect();
        match words.as_slice() {
            [orig] => names.push((orig.to_string(), orig.to_string())),
            [orig, "as", bound] => names.push((orig.to_string(), bound.to_string())),
            _ => {}
        }
    }
    Some((level, (!module.is_empty()).then_some(module), names))
}

/// Resolves and evaluates module constants under a source root, as
/// `sys.path.insert(0, REPO_ROOT); from pkg.mod import NAME` would.
pub struct Loader {
    root: PathBuf,
    cache: RefCell<HashMap<String, Rc<ModuleSrc>>>,
    executing: RefCell<Vec<(String, usize)>>,
    executed: RefCell<std::collections::HashSet<String>>,
}

/// The absolute module a `from <dots><module> import` in `module` names.
fn resolve_relative(module: &ModuleSrc, level: usize, from: Option<&str>) -> String {
    if level == 0 {
        return from.unwrap_or_default().to_string();
    }
    let mut package: Vec<&str> = module.name.split('.').collect();
    if !module.is_package {
        package.pop();
    }
    for _ in 1..level {
        package.pop();
    }
    let mut dotted = package.join(".");
    if let Some(from) = from {
        if !dotted.is_empty() {
            dotted.push('.');
        }
        dotted.push_str(from);
    }
    dotted
}

/// Why a static import/evaluation failed: an exception CPython would also
/// raise, or a construct the static evaluator does not model.
pub enum EvalError {
    Python(String),
    Unsupported(String),
}

fn exact_child(dir: &Path, name: &str) -> Option<PathBuf> {
    let entries = std::fs::read_dir(dir).ok()?;
    for entry in entries.flatten() {
        if entry.file_name().to_str() == Some(name) {
            return Some(entry.path());
        }
    }
    None
}

impl Loader {
    pub fn new(root: &Path) -> Loader {
        Loader {
            root: root.to_path_buf(),
            cache: RefCell::new(HashMap::new()),
            executing: RefCell::new(Vec::new()),
            executed: RefCell::new(std::collections::HashSet::new()),
        }
    }

    /// Locate and parse `dotted` (each package's `__init__.py` is not
    /// executed; only the requested module's constants are evaluated).
    pub fn module(&self, dotted: &str) -> Result<Rc<ModuleSrc>, EvalError> {
        if let Some(found) = self.cache.borrow().get(dotted) {
            return Ok(found.clone());
        }
        let parts: Vec<&str> = dotted.split('.').collect();
        let mut dir = self.root.clone();
        let mut file: Option<PathBuf> = None;
        let mut is_package = true;
        for (k, part) in parts.iter().enumerate() {
            let so_far = parts[..=k].join(".");
            if !is_package {
                return Err(EvalError::Python(format!(
                    "ModuleNotFoundError: No module named '{so_far}'; '{}' is not a package",
                    parts[..k].join(".")
                )));
            }
            let sub = exact_child(&dir, part).filter(|p| p.is_dir());
            let init = sub.as_ref().and_then(|d| exact_child(d, "__init__.py")).filter(|p| p.is_file());
            if let (Some(sub), Some(init)) = (sub.clone(), init) {
                dir = sub;
                file = Some(init);
                is_package = true;
            } else if let Some(module_file) = exact_child(&dir, &format!("{part}.py")).filter(|p| p.is_file()) {
                file = Some(module_file);
                is_package = false;
            } else if let Some(sub) = sub {
                dir = sub;
                file = None;
                is_package = true;
            } else {
                return Err(EvalError::Python(format!("ModuleNotFoundError: No module named '{so_far}'")));
            }
        }
        let stmts = match &file {
            None => Vec::new(),
            Some(path) => {
                // Import decodes source with BOM detection (PEP 263).
                let source = pyio::read_text_utf8(path).map_err(EvalError::Python)?;
                let source = source.strip_prefix('\u{feff}').unwrap_or(&source);
                pytok::parse_blocks(source).map_err(|e| EvalError::Python(e.exception_line()))?
            }
        };
        let module = Rc::new(ModuleSrc { name: dotted.to_string(), file, is_package, stmts });
        self.cache.borrow_mut().insert(dotted.to_string(), module.clone());
        Ok(module)
    }

    /// `from <dotted> import <name>`, evaluated. Like CPython it first
    /// "executes" the packages and module involved, which here means
    /// resolving their module-level imports in order (see `exec_module`).
    pub fn import_from(&self, dotted: &str, name: &str) -> Result<Val, EvalError> {
        self.exec_module(dotted)?;
        let module = self.module(dotted)?;
        self.lookup(&module, name, 0, true)
    }

    /// Whether `dotted`'s first component is a package/module in the tree
    /// (otherwise it is stdlib or installed, which the port does not model).
    fn in_tree(&self, dotted: &str) -> bool {
        let top = dotted.split('.').next().unwrap_or("");
        exact_child(&self.root, top).is_some() || exact_child(&self.root, &format!("{top}.py")).is_some()
    }

    /// The import-time effects that can fail: `import x.y` and
    /// `from x import a, b` statements at module level (not inside
    /// `try`/`if` blocks or functions), run in statement order, parents
    /// first, with CPython's view of a module that is still initializing
    /// (only names bound by the statements executed so far exist).
    fn exec_module(&self, dotted: &str) -> Result<(), EvalError> {
        if self.executed.borrow().contains(dotted) || self.executing.borrow().iter().any(|(m, _)| m == dotted) {
            return Ok(());
        }
        if let Some((parent, _)) = dotted.rsplit_once('.') {
            self.exec_module(parent)?;
            if self.executed.borrow().contains(dotted) {
                return Ok(());
            }
        }
        let module = self.module(dotted)?;
        self.executing.borrow_mut().push((dotted.to_string(), 0));
        let result = self.exec_statements(&module);
        self.executing.borrow_mut().pop();
        result?;
        self.executed.borrow_mut().insert(dotted.to_string());
        Ok(())
    }

    fn exec_statements(&self, module: &ModuleSrc) -> Result<(), EvalError> {
        for (index, stmt) in module.stmts.iter().enumerate() {
            if let Some(top) = self.executing.borrow_mut().last_mut() {
                top.1 = index;
            }
            if !matches!(classify_line(stmt), Line::Simple) {
                continue;
            }
            for part in simple_parts(stmt) {
                match classify_simple(part) {
                    Simple::ImportFrom(toks) => {
                        let Some((level, from, names)) = parse_import_from(toks) else { continue };
                        if level == 0 && from.as_deref() == Some("__future__") {
                            continue;
                        }
                        let target = resolve_relative(module, level, from.as_deref());
                        if level == 0 && !self.in_tree(&target) {
                            continue;
                        }
                        self.exec_module(&target)?;
                        let source = self.module(&target)?;
                        for (orig, _) in names {
                            if orig == "*" {
                                continue;
                            }
                            self.check_name(&source, &orig)?;
                        }
                    }
                    Simple::Import(toks) => {
                        for clause in split_top(&toks[1..], ",") {
                            let mut dotted = String::new();
                            for tok in clause {
                                if tok.is_name("as") {
                                    break;
                                }
                                dotted.push_str(&tok.text);
                            }
                            if !dotted.is_empty() && self.in_tree(&dotted) {
                                self.exec_module(&dotted)?;
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }

    /// `from <source> import <name>` must find the name bound (so far, if
    /// `source` is still initializing) or a submodule of that name.
    fn check_name(&self, source: &ModuleSrc, name: &str) -> Result<(), EvalError> {
        let partial = self.executing.borrow().iter().find(|(m, _)| *m == source.name).map(|(_, i)| *i);
        let limit = partial.unwrap_or(source.stmts.len());
        if source.binding_upto(name, limit).is_some() || source.has_dynamic_names() {
            return Ok(());
        }
        let sub = format!("{}.{name}", source.name);
        if source.is_package && self.module(&sub).is_ok() {
            return self.exec_module(&sub);
        }
        let location = match &source.file {
            Some(path) => pyio::path_str(path),
            None => "unknown location".to_string(),
        };
        Err(EvalError::Python(match partial {
            Some(_) => format!(
                "ImportError: cannot import name '{name}' from partially initialized module '{}' (most likely due to a \
                 circular import) ({location})",
                source.name
            ),
            None => format!("ImportError: cannot import name '{name}' from '{}' ({location})", source.name),
        }))
    }

    /// The value bound to `name` in `module`. A name the module does not
    /// bind is an `ImportError` for `from module import name`
    /// (`from_import`); inside an expression it is left opaque (it may be
    /// a builtin).
    fn lookup(&self, module: &ModuleSrc, name: &str, depth: usize, from_import: bool) -> Result<Val, EvalError> {
        if depth > 64 {
            return Ok(Val::Opaque(format!("{}.{name} (reference cycle)", module.name)));
        }
        let opaque = |what: &str| Ok(Val::Opaque(format!("{}.{name} ({what})", module.name)));
        match module.binding(name) {
            Some(Binding::Expr(toks)) => match parse_expr(&toks) {
                Some(expr) => self.eval(module, &expr, depth + 1),
                None => opaque("not a literal expression"),
            },
            Some(Binding::Import { level, module: from, name: orig }) => {
                let target = resolve_relative(module, level, from.as_deref());
                let inner = self.module(&target)?;
                self.lookup(&inner, &orig, depth + 1, true)
            }
            Some(Binding::Opaque(what)) => opaque(&format!("bound by {what}")),
            None if !from_import => opaque("not a module-level literal binding"),
            None => {
                if module.is_package && self.module(&format!("{}.{name}", module.name)).is_ok() {
                    return opaque("a submodule");
                }
                let location = match &module.file {
                    Some(path) => pyio::path_str(path),
                    None => "unknown location".to_string(),
                };
                Err(EvalError::Python(format!(
                    "ImportError: cannot import name '{name}' from '{}' ({location})",
                    module.name
                )))
            }
        }
    }

    fn eval(&self, module: &ModuleSrc, expr: &Expr, depth: usize) -> Result<Val, EvalError> {
        let opaque = |what: &str| Val::Opaque(format!("{what} in {}", module.name));
        let needed = |what: String| EvalError::Unsupported(format!("{what} (needed to build a value in {})", module.name));
        Ok(match expr {
            Expr::Name(name) => match name.as_str() {
                "None" => Val::None,
                "True" => Val::Bool(true),
                "False" => Val::Bool(false),
                _ => self.lookup(module, name, depth, false)?,
            },
            Expr::Str(text) => Val::Str(text.clone()),
            Expr::Num(text) => parse_number(text).unwrap_or_else(|| opaque(&format!("number {text}"))),
            Expr::Tuple(items) => Val::Tuple(self.eval_all(module, items, depth)?),
            Expr::List(items) => Val::List(self.eval_all(module, items, depth)?),
            Expr::Dict(items) => {
                let mut out = Vec::new();
                for (key, value) in items {
                    let value = self.eval(module, value, depth)?;
                    match key {
                        Some(key) => {
                            let key = self.eval(module, key, depth)?;
                            key.known().map_err(needed)?;
                            dict_insert(&mut out, key, value);
                        }
                        None => match value {
                            Val::Dict(pairs) => {
                                for (k, v) in pairs {
                                    dict_insert(&mut out, k, v);
                                }
                            }
                            Val::Opaque(what) => return Err(needed(what)),
                            other => {
                                return Err(EvalError::Python(format!(
                                    "TypeError: '{}' object is not a mapping",
                                    other.type_name()
                                )))
                            }
                        },
                    }
                }
                Val::Dict(out)
            }
            Expr::BinOp(op, left, right) => {
                let left = self.eval(module, left, depth)?;
                let right = self.eval(module, right, depth)?;
                for side in [&left, &right] {
                    if let Val::Opaque(what) = side {
                        return Ok(Val::Opaque(what.clone()));
                    }
                }
                match (op, left, right) {
                    ('|', Val::Dict(mut a), Val::Dict(b)) => {
                        for (k, v) in b {
                            dict_insert(&mut a, k, v);
                        }
                        Val::Dict(a)
                    }
                    ('|', Val::Int(a), Val::Int(b)) => Val::Int(a | b),
                    ('+', Val::Str(a), Val::Str(b)) => Val::Str(a + &b),
                    ('+', Val::List(mut a), Val::List(b)) => {
                        a.extend(b);
                        Val::List(a)
                    }
                    ('+', Val::Tuple(mut a), Val::Tuple(b)) => {
                        a.extend(b);
                        Val::Tuple(a)
                    }
                    ('+', Val::Int(a), Val::Int(b)) => Val::Int(a + b),
                    ('-', Val::Int(a), Val::Int(b)) => Val::Int(a - b),
                    (op, a, b) => {
                        return Err(EvalError::Python(format!(
                            "TypeError: unsupported operand type(s) for {op}: '{}' and '{}'",
                            a.type_name(),
                            b.type_name()
                        )))
                    }
                }
            }
            Expr::Neg(inner) => match self.eval(module, inner, depth)? {
                Val::Int(i) => Val::Int(-i),
                Val::Float(f) => Val::Float(-f),
                Val::Bool(b) => Val::Int(-(b as i128)),
                Val::Opaque(what) => Val::Opaque(what),
                other => {
                    return Err(EvalError::Python(format!(
                        "TypeError: bad operand type for unary -: '{}'",
                        other.type_name()
                    )))
                }
            },
            Expr::Pos(inner) => self.eval(module, inner, depth)?,
            Expr::Bytes => opaque("a bytes literal"),
            Expr::FString => opaque("an f-string"),
            Expr::Attr(..) => opaque("an attribute access"),
            Expr::Call(..) => opaque("a call"),
            Expr::Subscript(..) => opaque("a subscript"),
        })
    }

    fn eval_all(&self, module: &ModuleSrc, items: &[Expr], depth: usize) -> Result<Vec<Val>, EvalError> {
        items.iter().map(|item| self.eval(module, item, depth)).collect()
    }
}

fn parse_number(text: &str) -> Option<Val> {
    let plain: String = text.chars().filter(|c| *c != '_').collect();
    let lower = plain.to_ascii_lowercase();
    if lower.ends_with('j') {
        return None;
    }
    for (prefix, radix) in [("0x", 16), ("0o", 8), ("0b", 2)] {
        if let Some(digits) = lower.strip_prefix(prefix) {
            return i128::from_str_radix(digits, radix).ok().map(Val::Int);
        }
    }
    if lower.contains(['.', 'e']) {
        return lower.parse::<f64>().ok().map(Val::Float);
    }
    lower.parse::<i128>().ok().map(Val::Int)
}

/// A `ClassDef` named `name`, searched breadth-first like `ast.walk`.
pub fn find_class_bfs<'a>(stmts: &'a [Stmt], name: &str) -> Option<&'a Stmt> {
    let mut queue: std::collections::VecDeque<&Stmt> = stmts.iter().collect();
    while let Some(stmt) = queue.pop_front() {
        if let Line::Class { name: n } = classify_line(stmt) {
            if n == name {
                return Some(stmt);
            }
        }
        queue.extend(stmt.body.iter());
    }
    None
}
