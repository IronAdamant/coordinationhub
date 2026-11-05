//! Std-only port of `scripts/update_dispatch_floor.py`: regenerate
//! `tests/dispatch_coverage_floor.json` from the test suite.
//!
//! ```text
//! cargo run --release --example update_dispatch_floor -- [--root DIR]
//! ```
//!
//! The floor maps each `TOOL_DISPATCH` key (read from the dict display in
//! `coordinationhub/dispatch.py`) to the sorted `module.py::Class::test_x` /
//! `module.py::test_x` names whose innermost enclosing function is a
//! `test_*` and which call `engine.<tool>(...)` or
//! `dispatch_tool(<arg>, "<tool>", ...)`, scanning `tests/test_*.py` and
//! `tests/conftest.py`. Like the Python script it takes no other arguments
//! (any are ignored); `--root` (default: this crate's directory) is the
//! checkout it ran in.

#[allow(dead_code)]
mod pyport;

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use pyport::pyast::{self, classify_line, dict_display_keys, matching, split_top, str_constant, strip_parens, Line};
use pyport::pyio::{self, print_err, print_out, uncaught};
use pyport::pyjson;
use pyport::pytok::{is_keyword, Kind, Stmt, Tok};

fn load_tool_dispatch_keys(root: &Path) -> BTreeSet<String> {
    let path = root.join("coordinationhub").join("dispatch.py");
    let text = pyio::read_text_utf8(&path).unwrap_or_else(|exc| uncaught(&exc));
    let stmts = pyast::ast_parse(&text).unwrap_or_else(|e| uncaught(&e.exception_line()));
    match dict_display_keys(&stmts, "TOOL_DISPATCH") {
        Some(keys) => keys.into_iter().flatten().collect(),
        None => uncaught("RuntimeError: Could not locate TOOL_DISPATCH = {...} in coordinationhub/dispatch.py"),
    }
}

/// Does the token before an expression starting at `open` end an operand
/// (so that `(...)` there would be a call's argument list, not a group)?
fn ends_operand(tok: Option<&Tok>) -> bool {
    match tok {
        None => false,
        Some(t) => match t.kind {
            Kind::Name => !is_keyword(&t.text) || matches!(t.text.as_str(), "True" | "False" | "None"),
            Kind::Number | Kind::Str | Kind::FEnd => true,
            Kind::Op => matches!(t.text.as_str(), ")" | "]" | "}"),
            _ => false,
        },
    }
}

/// If the primary ending at `end` (inclusive) is the bare name `name`,
/// possibly wrapped in grouping parentheses, return its start index.
fn bare_name_before(toks: &[Tok], end: usize, name: &str) -> Option<usize> {
    let tok = &toks[end];
    if tok.kind == Kind::Name && tok.text == name {
        return Some(end);
    }
    if !tok.is_op(")") {
        return None;
    }
    let mut depth = 0i32;
    let mut open = None;
    for k in (0..=end).rev() {
        if toks[k].closes() {
            depth += 1;
        } else if toks[k].opens() {
            depth -= 1;
            if depth == 0 {
                open = Some(k);
                break;
            }
        }
    }
    let open = open?;
    if !toks[open].is_op("(") || matching(toks, open) != Some(end) {
        return None;
    }
    let inner = strip_parens(&toks[open + 1..end]);
    if inner.len() == 1 && inner[0].kind == Kind::Name && inner[0].text == name {
        let before = open.checked_sub(1).map(|i| &toks[i]);
        if !ends_operand(before) && !before.is_some_and(|t| t.is_op(".")) {
            return Some(open);
        }
    }
    None
}

/// The name at `start` begins an expression (it is not `x.<name>`, nor
/// the name a `def`/`class` statement defines).
fn starts_primary(toks: &[Tok], start: usize) -> bool {
    start == 0 || !(toks[start - 1].is_op(".") || toks[start - 1].is_name("def") || toks[start - 1].is_name("class"))
}

/// `_tool_from_call` for every call in `toks`.
fn tools_called(toks: &[Tok]) -> Vec<String> {
    let mut out = Vec::new();
    for i in 0..toks.len() {
        // Shape 1: engine.<tool>(...)
        if toks[i].is_op(".")
            && i > 0
            && toks.get(i + 1).is_some_and(|t| t.kind == Kind::Name)
            && toks.get(i + 2).is_some_and(|t| t.is_op("("))
        {
            if let Some(start) = bare_name_before(toks, i - 1, "engine") {
                if starts_primary(toks, start) {
                    out.push(toks[i + 1].text.clone());
                }
            }
        }
        // Shape 2: dispatch_tool(<arg>, "<tool>", ...)
        if toks[i].is_op("(") && i > 0 {
            let Some(start) = bare_name_before(toks, i - 1, "dispatch_tool") else { continue };
            if !starts_primary(toks, start) {
                continue;
            }
            let Some(close) = matching(toks, i) else { continue };
            let positional: Vec<&[Tok]> = split_top(&toks[i + 1..close], ",")
                .into_iter()
                .filter(|arg| !arg.is_empty())
                .filter(|arg| !arg[0].is_op("**"))
                .filter(|arg| !(arg.len() >= 2 && arg[0].kind == Kind::Name && arg[1].is_op("=")))
                .collect();
            if positional.len() >= 2 {
                if let Some(tool) = str_constant(positional[1]) {
                    out.push(tool);
                }
            }
        }
    }
    out
}

struct Visitor<'a> {
    mod_name: &'a str,
    classes: Vec<String>,
    funcs: Vec<String>,
    out: Vec<(String, String)>,
}

impl Visitor<'_> {
    fn enclosing_test(&self) -> Option<String> {
        let func = self.funcs.last()?;
        if !func.starts_with("test_") {
            return None;
        }
        Some(match self.classes.last() {
            Some(cls) => format!("{cls}::{func}"),
            None => func.clone(),
        })
    }

    fn scan(&mut self, toks: &[Tok]) {
        let Some(test) = self.enclosing_test() else { return };
        for tool in tools_called(toks) {
            self.out.push((tool, format!("{}::{test}", self.mod_name)));
        }
    }

    fn visit_block(&mut self, stmts: &[Stmt]) {
        let mut decorators: Vec<&Stmt> = Vec::new();
        for stmt in stmts {
            match classify_line(stmt) {
                Line::Decorator => decorators.push(stmt),
                Line::Def { name, .. } => {
                    self.funcs.push(name.to_string());
                    for decorator in decorators.drain(..) {
                        self.scan(&decorator.toks);
                    }
                    self.scan(&stmt.toks);
                    self.visit_block(&stmt.body);
                    self.funcs.pop();
                }
                Line::Class { name } => {
                    self.classes.push(name.to_string());
                    for decorator in decorators.drain(..) {
                        self.scan(&decorator.toks);
                    }
                    self.scan(&stmt.toks);
                    self.visit_block(&stmt.body);
                    self.classes.pop();
                }
                _ => {
                    for decorator in decorators.drain(..) {
                        self.scan(&decorator.toks);
                    }
                    self.scan(&stmt.toks);
                    self.visit_block(&stmt.body);
                }
            }
        }
    }
}

fn collect_callsites(stmts: &[Stmt], mod_name: &str) -> Vec<(String, String)> {
    let mut visitor = Visitor { mod_name, classes: Vec::new(), funcs: Vec::new(), out: Vec::new() };
    visitor.visit_block(stmts);
    visitor.out
}

/// `sorted(TESTS_DIR.glob("*.py"))` as file names.
fn test_files(tests: &Path) -> Vec<String> {
    let Ok(entries) = std::fs::read_dir(tests) else { return Vec::new() };
    let mut names: Vec<String> = entries
        .flatten()
        .map(|e| e.file_name().to_string_lossy().to_string())
        .filter(|n| if cfg!(windows) { n.to_lowercase().ends_with(".py") } else { n.ends_with(".py") })
        .collect();
    if cfg!(windows) {
        names.sort_by_key(|n| n.to_lowercase());
    } else {
        names.sort();
    }
    names
}

fn build_floor(root: &Path) -> BTreeMap<String, Vec<String>> {
    let tools = load_tool_dispatch_keys(root);
    let mut floor: BTreeMap<String, BTreeSet<String>> = tools.into_iter().map(|t| (t, BTreeSet::new())).collect();
    let tests = root.join("tests");
    for name in test_files(&tests) {
        if !name.starts_with("test_") && name != "conftest.py" {
            continue;
        }
        let text = pyio::read_text_utf8(&tests.join(&name)).unwrap_or_else(|exc| uncaught(&exc));
        let Ok(stmts) = pyast::ast_parse(&text) else { continue };
        for (tool, qualified) in collect_callsites(&stmts, &name) {
            if let Some(names) = floor.get_mut(&tool) {
                names.insert(qualified);
            }
        }
    }
    floor.into_iter().map(|(tool, names)| (tool, names.into_iter().collect())).collect()
}

fn main() {
    // The Python script reads no arguments; only the port's --root is used.
    let (root, _ignored) = pyport::take_root(std::env::args().skip(1).collect());
    let floor = build_floor(&root);
    let floor_path = root.join("tests").join("dispatch_coverage_floor.json");
    let text = format!("{}\n", pyjson::dumps_str_lists(&floor));
    pyio::write_text_utf8(&floor_path, &text).unwrap_or_else(|exc| uncaught(&exc));
    let total: usize = floor.values().map(Vec::len).sum();
    let empty: Vec<&String> = floor.iter().filter(|(_, v)| v.is_empty()).map(|(k, _)| k).collect();
    let rel = pyio::join_os(&["tests".to_string(), "dispatch_coverage_floor.json".to_string()]);
    print_out(&format!("Wrote {rel} — {} tools, {total} test references.", floor.len()));
    if !empty.is_empty() {
        print_err(&format!(
            "NOTE: {} tool(s) have zero structured callsites in tests/. They still pass the meta-test if a \
             docstring or string-literal mention exists, but the floor cannot ratchet on them: {}",
            empty.len(),
            empty.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")
        ));
    }
    pyio::exit(0);
}
