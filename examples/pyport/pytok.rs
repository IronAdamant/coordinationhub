//! A tokenizer for Python 3.12 source and its logical-line / block
//! structure: enough of CPython's tokenizer for the ported scripts to
//! answer their `ast` questions the way `ast.parse` would.
//!
//! It handles names, numbers, operators, brackets, comments, explicit and
//! implicit line joining, INDENT/DEDENT (with CPython's tab rules and
//! `TabError`/`IndentationError` checks), and string literals of every
//! prefix: escapes are decoded for `str` literals, and f-strings are
//! tokenized PEP 701 style (`FStart`, the replacement-field expression
//! tokens separated by `FMid`, `FEnd`), so calls inside f-strings are seen
//! as `ast.walk` sees them. `\N{...}` escapes are kept verbatim (the
//! Unicode name table is not reproduced). Source that CPython's tokenizer
//! rejects is reported as a `SyntaxError`; grammar errors past the
//! tokenizer (e.g. `x = = 1`) are not detected.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Name,
    Number,
    Str,
    FStart,
    FMid,
    FEnd,
    Op,
    Newline,
    Indent,
    Dedent,
}

#[derive(Clone, Debug)]
pub struct Tok {
    pub kind: Kind,
    /// Name/Number/Op: the source text. Str: the literal's value.
    pub text: String,
    /// Str: a bytes literal.
    pub bytes: bool,
    pub line: usize,
}

impl Tok {
    pub fn is_op(&self, op: &str) -> bool {
        self.kind == Kind::Op && self.text == op
    }

    pub fn is_name(&self, name: &str) -> bool {
        self.kind == Kind::Name && self.text == name
    }

    /// Opening bracket for depth counting (f-string bounds included).
    pub fn opens(&self) -> bool {
        self.kind == Kind::FStart || (self.kind == Kind::Op && matches!(self.text.as_str(), "(" | "[" | "{"))
    }

    pub fn closes(&self) -> bool {
        self.kind == Kind::FEnd || (self.kind == Kind::Op && matches!(self.text.as_str(), ")" | "]" | "}"))
    }
}

#[derive(Debug)]
pub struct SyntaxError {
    pub msg: String,
    pub line: usize,
}

impl SyntaxError {
    /// The exception class CPython raises for this message.
    pub fn kind(&self) -> &'static str {
        if self.msg.starts_with("inconsistent use of tabs") {
            "TabError"
        } else if self.msg.starts_with("unindent does not match")
            || self.msg.starts_with("unexpected indent")
            || self.msg.starts_with("expected an indented block")
        {
            "IndentationError"
        } else {
            "SyntaxError"
        }
    }

    /// The final line of the traceback `ast.parse` would end with.
    pub fn exception_line(&self) -> String {
        format!("{}: {}", self.kind(), self.msg)
    }
}

pub const KEYWORDS: &[&str] = &[
    "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del",
    "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
    "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
];

pub fn is_keyword(text: &str) -> bool {
    KEYWORDS.contains(&text)
}

const OPS3: &[&str] = &["**=", "//=", ">>=", "<<=", "..."];
const OPS2: &[&str] = &[
    "**", "//", ">>", "<<", "<=", ">=", "==", "!=", "->", ":=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "@=",
];
const OPS1: &str = "()[]{},:.;@=+-*/%&|^~<>";

fn ident_start(ch: char) -> bool {
    ch == '_' || ch.is_alphabetic()
}

fn ident_continue(ch: char) -> bool {
    ch == '_' || ch.is_alphanumeric()
}

struct Lexer {
    chars: Vec<char>,
    pos: usize,
    line: usize,
    toks: Vec<Tok>,
    warnings: Vec<(usize, String)>,
}

fn err<T>(msg: impl Into<String>, line: usize) -> Result<T, SyntaxError> {
    Err(SyntaxError { msg: msg.into(), line })
}

/// `(line, message)` of each `SyntaxWarning` met while tokenizing.
pub type Warnings = Vec<(usize, String)>;

/// Tokenize `source` (newlines are normalized first, as `ast.parse` does).
pub fn tokenize(source: &str) -> Result<Vec<Tok>, SyntaxError> {
    tokenize_warn(source).0
}

/// [`tokenize`], plus the warnings emitted before it finished or failed.
pub fn tokenize_warn(source: &str) -> (Result<Vec<Tok>, SyntaxError>, Warnings) {
    if source.contains('\0') {
        return (err("source code string cannot contain null bytes", 1), Vec::new());
    }
    // A leading U+FEFF is not skipped: `ast.parse(str)` rejects it as a
    // non-printable character, as it does anywhere outside a literal.
    let text = source.replace("\r\n", "\n").replace('\r', "\n");
    let chars: Vec<char> = text.chars().collect();
    let mut lexer = Lexer { chars, pos: 0, line: 1, toks: Vec::new(), warnings: Vec::new() };
    let result = lexer.run();
    let warnings = std::mem::take(&mut lexer.warnings);
    (result.map(|_| lexer.toks), warnings)
}

impl Lexer {
    fn peek(&self) -> Option<char> {
        self.chars.get(self.pos).copied()
    }

    fn peek_at(&self, offset: usize) -> Option<char> {
        self.chars.get(self.pos + offset).copied()
    }

    fn push(&mut self, kind: Kind, text: impl Into<String>, line: usize) {
        self.toks.push(Tok { kind, text: text.into(), bytes: false, line });
    }

    fn run(&mut self) -> Result<(), SyntaxError> {
        let mut indents: Vec<(usize, usize)> = vec![(0, 0)];
        let mut brackets: Vec<(char, usize)> = Vec::new();
        let mut at_bol = true;
        let mut line_has_tokens = false;
        loop {
            if at_bol && brackets.is_empty() {
                at_bol = false;
                let (mut col, mut alt) = (0usize, 0usize);
                while let Some(ch) = self.peek() {
                    match ch {
                        ' ' => {
                            col += 1;
                            alt += 1;
                        }
                        '\t' => {
                            col = (col / 8 + 1) * 8;
                            alt += 1;
                        }
                        '\x0c' => {
                            col = 0;
                            alt = 0;
                        }
                        _ => break,
                    }
                    self.pos += 1;
                }
                match self.peek() {
                    None => break,
                    Some('#') | Some('\n') => {
                        while let Some(ch) = self.peek() {
                            if ch == '\n' {
                                break;
                            }
                            self.pos += 1;
                        }
                        if self.peek() == Some('\n') {
                            self.pos += 1;
                            self.line += 1;
                        }
                        at_bol = true;
                        continue;
                    }
                    _ => {}
                }
                let (top_col, top_alt) = *indents.last().expect("indent stack");
                if col == top_col {
                    if alt != top_alt {
                        return err("inconsistent use of tabs and spaces in indentation", self.line);
                    }
                } else if col > top_col {
                    if alt <= top_alt {
                        return err("inconsistent use of tabs and spaces in indentation", self.line);
                    }
                    indents.push((col, alt));
                    self.push(Kind::Indent, "", self.line);
                } else {
                    while indents.len() > 1 && col < indents.last().expect("indent").0 {
                        indents.pop();
                        self.push(Kind::Dedent, "", self.line);
                    }
                    let (top_col, top_alt) = *indents.last().expect("indent stack");
                    if col != top_col {
                        return err("unindent does not match any outer indentation level", self.line);
                    }
                    if alt != top_alt {
                        return err("inconsistent use of tabs and spaces in indentation", self.line);
                    }
                }
            }
            let Some(ch) = self.peek() else { break };
            match ch {
                ' ' | '\t' | '\x0c' => self.pos += 1,
                '#' => {
                    while let Some(ch) = self.peek() {
                        if ch == '\n' {
                            break;
                        }
                        self.pos += 1;
                    }
                }
                '\n' => {
                    self.pos += 1;
                    if brackets.is_empty() {
                        if line_has_tokens {
                            self.push(Kind::Newline, "", self.line);
                            line_has_tokens = false;
                        }
                        at_bol = true;
                    }
                    self.line += 1;
                }
                '\\' => match self.peek_at(1) {
                    Some('\n') => {
                        self.pos += 2;
                        self.line += 1;
                    }
                    None => return err("unexpected EOF while parsing", self.line),
                    Some(_) => return err("unexpected character after line continuation character", self.line),
                },
                _ => {
                    self.lex_token(&mut brackets, false)?;
                    line_has_tokens = true;
                }
            }
        }
        if let Some((open, line)) = brackets.last() {
            return err(format!("'{open}' was never closed"), *line);
        }
        if line_has_tokens {
            self.push(Kind::Newline, "", self.line);
        }
        while indents.len() > 1 {
            indents.pop();
            self.push(Kind::Dedent, "", self.line);
        }
        Ok(())
    }

    /// One name / number / string / operator token.
    fn lex_token(&mut self, brackets: &mut Vec<(char, usize)>, in_fstring: bool) -> Result<(), SyntaxError> {
        let ch = self.peek().expect("token start");
        let line = self.line;
        if ident_start(ch) {
            let start = self.pos;
            while self.peek().is_some_and(ident_continue) {
                self.pos += 1;
            }
            let word: String = self.chars[start..self.pos].iter().collect();
            if matches!(self.peek(), Some('"') | Some('\'')) {
                let lower = word.to_ascii_lowercase();
                if matches!(lower.as_str(), "r" | "u" | "b" | "br" | "rb" | "f" | "fr" | "rf") {
                    return self.lex_string(&lower);
                }
            }
            self.push(Kind::Name, word, line);
            return Ok(());
        }
        if ch.is_ascii_digit() || (ch == '.' && self.peek_at(1).is_some_and(|c| c.is_ascii_digit())) {
            self.lex_number();
            return Ok(());
        }
        if ch == '"' || ch == '\'' {
            return self.lex_string("");
        }
        let rest: String = self.chars[self.pos..(self.pos + 3).min(self.chars.len())].iter().collect();
        for op in OPS3.iter().chain(OPS2.iter()) {
            if rest.starts_with(op) {
                self.pos += op.chars().count();
                self.push(Kind::Op, *op, line);
                return Ok(());
            }
        }
        if OPS1.contains(ch) || (in_fstring && ch == '!') {
            self.pos += 1;
            match ch {
                '(' | '[' | '{' => brackets.push((ch, line)),
                ')' | ']' | '}' => {
                    let want = match ch {
                        ')' => '(',
                        ']' => '[',
                        _ => '{',
                    };
                    match brackets.pop() {
                        Some((open, _)) if open == want => {}
                        Some((open, open_line)) => {
                            let at = if open_line == line { String::new() } else { format!(" on line {open_line}") };
                            return err(
                                format!("closing parenthesis '{ch}' does not match opening parenthesis '{open}'{at}"),
                                line,
                            );
                        }
                        None => return err(format!("unmatched '{ch}'"), line),
                    }
                }
                _ => {}
            }
            self.push(Kind::Op, ch.to_string(), line);
            return Ok(());
        }
        if ch.is_ascii() {
            err("invalid syntax", line)
        } else if !printable(ch) {
            err(format!("invalid non-printable character U+{:04X}", ch as u32), line)
        } else {
            err(format!("invalid character '{ch}' (U+{:04X})", ch as u32), line)
        }
    }

    fn lex_number(&mut self) {
        let start = self.pos;
        let line = self.line;
        let first = self.peek().expect("digit");
        if first == '0' && matches!(self.peek_at(1), Some('x' | 'X' | 'o' | 'O' | 'b' | 'B')) {
            self.pos += 2;
            while self.peek().is_some_and(|c| c.is_ascii_hexdigit() || c == '_') {
                self.pos += 1;
            }
        } else {
            while self.peek().is_some_and(|c| c.is_ascii_digit() || c == '_') {
                self.pos += 1;
            }
            if self.peek() == Some('.') {
                self.pos += 1;
                while self.peek().is_some_and(|c| c.is_ascii_digit() || c == '_') {
                    self.pos += 1;
                }
            }
            if matches!(self.peek(), Some('e' | 'E')) {
                let sign = matches!(self.peek_at(1), Some('+' | '-'));
                let digit_at = if sign { 2 } else { 1 };
                if self.peek_at(digit_at).is_some_and(|c| c.is_ascii_digit()) {
                    self.pos += digit_at;
                    while self.peek().is_some_and(|c| c.is_ascii_digit() || c == '_') {
                        self.pos += 1;
                    }
                }
            }
            if matches!(self.peek(), Some('j' | 'J')) {
                self.pos += 1;
            }
        }
        let text: String = self.chars[start..self.pos].iter().collect();
        self.push(Kind::Number, text, line);
    }

    fn at_close(&self, quote: char, triple: bool) -> bool {
        self.peek() == Some(quote)
            && (!triple || (self.peek_at(1) == Some(quote) && self.peek_at(2) == Some(quote)))
    }

    fn lex_string(&mut self, prefix: &str) -> Result<(), SyntaxError> {
        let raw = prefix.contains('r');
        let is_bytes = prefix.contains('b');
        let quote = self.peek().expect("quote");
        let triple = self.peek_at(1) == Some(quote) && self.peek_at(2) == Some(quote);
        let start_line = self.line;
        self.pos += if triple { 3 } else { 1 };
        if prefix.contains('f') {
            return self.lex_fstring(raw, quote, triple, start_line);
        }
        let mut body = String::new();
        loop {
            let Some(ch) = self.peek() else {
                return err(unterminated(triple, self.line), start_line);
            };
            if ch == '\\' {
                body.push('\\');
                self.pos += 1;
                if let Some(next) = self.peek() {
                    body.push(next);
                    self.pos += 1;
                    if next == '\n' {
                        self.line += 1;
                    }
                }
                continue;
            }
            if self.at_close(quote, triple) {
                self.pos += if triple { 3 } else { 1 };
                break;
            }
            if ch == '\n' {
                if !triple {
                    return err(unterminated(false, self.line), start_line);
                }
                self.line += 1;
            }
            body.push(ch);
            self.pos += 1;
        }
        if is_bytes && !body.is_ascii() {
            return err("bytes can only contain ASCII literal characters", start_line);
        }
        let value = if raw {
            body
        } else {
            let (value, warning) = decode_escapes(&body, is_bytes, start_line)?;
            if let Some(warning) = warning {
                self.warnings.push((start_line, warning));
            }
            value
        };
        self.toks.push(Tok { kind: Kind::Str, text: value, bytes: is_bytes, line: start_line });
        Ok(())
    }

    fn lex_fstring(&mut self, raw: bool, quote: char, triple: bool, start_line: usize) -> Result<(), SyntaxError> {
        self.push(Kind::FStart, "", start_line);
        loop {
            let Some(ch) = self.peek() else {
                return err(unterminated(triple, self.line), start_line);
            };
            if self.at_close(quote, triple) {
                self.pos += if triple { 3 } else { 1 };
                self.push(Kind::FEnd, "", self.line);
                return Ok(());
            }
            match ch {
                '\n' if !triple => return err(unterminated(false, self.line), start_line),
                '\n' => {
                    self.line += 1;
                    self.pos += 1;
                }
                '\\' => {
                    self.pos += 1;
                    match self.peek() {
                        None => return err(unterminated(triple, self.line), start_line),
                        Some('N') if !raw && self.peek_at(1) == Some('{') => {
                            while let Some(c) = self.peek() {
                                self.pos += 1;
                                if c == '}' {
                                    break;
                                }
                            }
                        }
                        Some('{') | Some('}') => {}
                        Some(next) => {
                            self.pos += 1;
                            if next == '\n' {
                                self.line += 1;
                            }
                        }
                    }
                }
                '{' => {
                    if self.peek_at(1) == Some('{') {
                        self.pos += 2;
                        continue;
                    }
                    self.pos += 1;
                    self.push(Kind::FMid, "", self.line);
                    self.lex_field(quote, triple)?;
                    self.push(Kind::FMid, "", self.line);
                }
                '}' => {
                    if self.peek_at(1) == Some('}') {
                        self.pos += 2;
                        continue;
                    }
                    return err("f-string: single '}' is not allowed", self.line);
                }
                _ => self.pos += 1,
            }
        }
    }

    /// A replacement field after its `{`: the expression, an optional
    /// `!conversion`, an optional `:format_spec`, and the closing `}`.
    fn lex_field(&mut self, quote: char, triple: bool) -> Result<(), SyntaxError> {
        let mut brackets: Vec<(char, usize)> = Vec::new();
        loop {
            while let Some(ch) = self.peek() {
                match ch {
                    ' ' | '\t' | '\x0c' => self.pos += 1,
                    '\n' => {
                        self.pos += 1;
                        self.line += 1;
                    }
                    '#' => {
                        while self.peek().is_some_and(|c| c != '\n') {
                            self.pos += 1;
                        }
                    }
                    '\\' if self.peek_at(1) == Some('\n') => {
                        self.pos += 2;
                        self.line += 1;
                    }
                    _ => break,
                }
            }
            let Some(ch) = self.peek() else {
                return err("f-string: expecting '}'", self.line);
            };
            if brackets.is_empty() {
                if ch == '}' {
                    self.pos += 1;
                    return Ok(());
                }
                if ch == '!' && self.peek_at(1) != Some('=') {
                    self.pos += 1;
                    while self.peek().is_some_and(ident_continue) {
                        self.pos += 1;
                    }
                    continue;
                }
                if ch == ':' {
                    self.pos += 1;
                    return self.lex_format_spec(quote, triple);
                }
            }
            // PEP 701: a quote here starts a nested literal, even the
            // enclosing f-string's own quote character.
            self.lex_token(&mut brackets, true)?;
        }
    }

    fn lex_format_spec(&mut self, quote: char, triple: bool) -> Result<(), SyntaxError> {
        loop {
            let Some(ch) = self.peek() else {
                return err("f-string: expecting '}'", self.line);
            };
            if self.at_close(quote, triple) {
                return err("f-string: expecting '}'", self.line);
            }
            match ch {
                '}' => {
                    self.pos += 1;
                    return Ok(());
                }
                '{' => {
                    self.pos += 1;
                    self.push(Kind::FMid, "", self.line);
                    self.lex_field(quote, triple)?;
                    self.push(Kind::FMid, "", self.line);
                }
                '\n' if !triple => return err("f-string: expecting '}'", self.line),
                '\n' => {
                    self.line += 1;
                    self.pos += 1;
                }
                '\\' => {
                    self.pos += 1;
                    if self.peek().is_some() {
                        self.pos += 1;
                    }
                }
                _ => self.pos += 1,
            }
        }
    }
}

/// `str.isprintable()` for one character (categories C*, Z* other than
/// the ASCII space are not printable).
fn printable(ch: char) -> bool {
    let code = ch as u32;
    if code < 0x20 || code == 0x7F {
        return false;
    }
    if code < 0x7F {
        return true;
    }
    !matches!(
        code,
        0x80..=0xA0
            | 0xAD
            | 0x0600..=0x0605
            | 0x061C
            | 0x06DD
            | 0x070F
            | 0x0890..=0x0891
            | 0x08E2
            | 0x1680
            | 0x180E
            | 0x2000..=0x200F
            | 0x2028..=0x202F
            | 0x205F..=0x2064
            | 0x2066..=0x206F
            | 0x3000
            | 0xD800..=0xF8FF
            | 0xFDD0..=0xFDEF
            | 0xFEFF
            | 0xFFF9..=0xFFFB
            | 0x110BD
            | 0x110CD
            | 0x13430..=0x1343F
            | 0x1BCA0..=0x1BCA3
            | 0x1D173..=0x1D17A
            | 0xE0001
            | 0xE0020..=0xE007F
            | 0xF0000..=0x10FFFF
    ) && (code & 0xFFFE) != 0xFFFE
}

fn unterminated(triple: bool, line: usize) -> String {
    if triple {
        format!("unterminated triple-quoted string literal (detected at line {line})")
    } else {
        format!("unterminated string literal (detected at line {line})")
    }
}

fn hex_value(digits: &[char]) -> Option<u32> {
    let text: String = digits.iter().collect();
    if text.chars().all(|c| c.is_ascii_hexdigit()) {
        u32::from_str_radix(&text, 16).ok()
    } else {
        None
    }
}

/// Decode the escapes of a non-raw literal body. The second value is the
/// `SyntaxWarning` CPython 3.12 emits for the literal's first invalid
/// escape (`'\d'`, or an octal escape above `\377`).
fn decode_escapes(body: &str, is_bytes: bool, line: usize) -> Result<(String, Option<String>), SyntaxError> {
    let chars: Vec<char> = body.chars().collect();
    let mut out = String::with_capacity(body.len());
    let mut warning: Option<String> = None;
    let mut i = 0;
    let bad = |what: &str| err::<(String, Option<String>)>(format!("(unicode error) truncated {what} escape"), line);
    while i < chars.len() {
        let ch = chars[i];
        if ch != '\\' || i + 1 >= chars.len() {
            out.push(ch);
            i += 1;
            continue;
        }
        let next = chars[i + 1];
        i += 2;
        match next {
            '\n' => {}
            '\\' => out.push('\\'),
            '\'' => out.push('\''),
            '"' => out.push('"'),
            'a' => out.push('\x07'),
            'b' => out.push('\x08'),
            'f' => out.push('\x0c'),
            'n' => out.push('\n'),
            'r' => out.push('\r'),
            't' => out.push('\t'),
            'v' => out.push('\x0b'),
            '0'..='7' => {
                let start = i - 1;
                let mut value = next.to_digit(8).expect("octal");
                let mut taken = 1;
                while taken < 3 && i < chars.len() && chars[i].is_digit(8) {
                    value = value * 8 + chars[i].to_digit(8).expect("octal");
                    i += 1;
                    taken += 1;
                }
                if value > 0o377 && warning.is_none() {
                    let digits: String = chars[start..i].iter().collect();
                    warning = Some(format!("invalid octal escape sequence '\\{digits}'"));
                }
                out.push(char::from_u32(if is_bytes { value & 0xFF } else { value }).unwrap_or('\u{fffd}'));
            }
            'N' if !is_bytes && chars.get(i) == Some(&'{') => {
                // \N{NAME}: kept verbatim (no Unicode name table here).
                out.push_str("\\N");
                while i < chars.len() {
                    out.push(chars[i]);
                    i += 1;
                    if chars[i - 1] == '}' {
                        break;
                    }
                }
            }
            'x' => {
                let Some(value) = chars.get(i..i + 2).and_then(hex_value) else {
                    return bad("\\xXX");
                };
                i += 2;
                out.push(char::from_u32(value).unwrap_or('\u{fffd}'));
            }
            'u' | 'U' if !is_bytes => {
                let width = if next == 'u' { 4 } else { 8 };
                let Some(value) = chars.get(i..i + width).and_then(hex_value) else {
                    return bad(if next == 'u' { "\\uXXXX" } else { "\\UXXXXXXXX" });
                };
                if value > 0x10FFFF {
                    return err("(unicode error) illegal Unicode character", line);
                }
                i += width;
                out.push(char::from_u32(value).unwrap_or('\u{fffd}'));
            }
            _ => {
                if warning.is_none() {
                    warning = Some(format!("invalid escape sequence '\\{next}'"));
                }
                out.push('\\');
                out.push(next);
            }
        }
    }
    Ok((out, warning))
}

/// One logical line and the indented block that follows it.
#[derive(Clone, Debug)]
pub struct Stmt {
    pub toks: Vec<Tok>,
    pub body: Vec<Stmt>,
    pub line: usize,
}

/// Tokenize and group into logical lines and blocks. A line ending in `:`
/// must be followed by an indented block and only such a line may be.
pub fn parse_blocks(source: &str) -> Result<Vec<Stmt>, SyntaxError> {
    parse_blocks_warn(source).0
}

/// [`parse_blocks`], plus the tokenizer's `SyntaxWarning`s.
pub fn parse_blocks_warn(source: &str) -> (Result<Vec<Stmt>, SyntaxError>, Warnings) {
    let (toks, warnings) = tokenize_warn(source);
    let result = toks.and_then(|toks| {
        let mut index = 0;
        block(&toks, &mut index, true)
    });
    (result, warnings)
}

fn block(toks: &[Tok], index: &mut usize, top: bool) -> Result<Vec<Stmt>, SyntaxError> {
    let mut out = Vec::new();
    loop {
        match toks.get(*index) {
            None => {
                return if top {
                    Ok(out)
                } else {
                    err("unexpected EOF while parsing", toks.last().map(|t| t.line).unwrap_or(1))
                };
            }
            Some(tok) if tok.kind == Kind::Dedent => {
                if top {
                    return err("unindent does not match any outer indentation level", tok.line);
                }
                *index += 1;
                return Ok(out);
            }
            Some(tok) if tok.kind == Kind::Indent => return err("unexpected indent", tok.line),
            _ => {}
        }
        let start = *index;
        while *index < toks.len() && toks[*index].kind != Kind::Newline {
            *index += 1;
        }
        let line_toks = toks[start..*index].to_vec();
        *index += 1;
        let line = line_toks.first().map(|t| t.line).unwrap_or(1);
        let ends_colon = line_toks.last().is_some_and(|t| t.is_op(":"));
        let mut stmt = Stmt { toks: line_toks, body: Vec::new(), line };
        if toks.get(*index).is_some_and(|t| t.kind == Kind::Indent) {
            if !ends_colon {
                return err("unexpected indent", toks[*index].line);
            }
            *index += 1;
            stmt.body = block(toks, index, false)?;
        } else if ends_colon {
            let words: Vec<&str> =
                stmt.toks.iter().take(2).filter(|t| t.kind == Kind::Name).map(|t| t.text.as_str()).collect();
            let keyword = match words.as_slice() {
                ["async", second, ..] => *second,
                [first, ..] => *first,
                [] => "",
            };
            let what = match keyword {
                "def" => format!("function definition on line {line}"),
                "class" => format!("class definition on line {line}"),
                other => format!("'{other}' statement on line {line}"),
            };
            return err(format!("expected an indented block after {what}"), line);
        }
        out.push(stmt);
    }
}
