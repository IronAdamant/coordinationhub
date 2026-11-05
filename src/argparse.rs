//! The argparse behaviour of CoordinationHub 0.8.0's command line.
//!
//! This is a port of the CPython 3.12 `argparse` code paths the 0.8.0 parser
//! uses: option matching with unique-prefix abbreviations, `-xyz` short
//! clusters, `--name=value`, negative-number positionals, `--`, `nargs` of
//! `?` and `+`, `int` and `float` conversion, `choices`, the error sentences,
//! and `HelpFormatter` (usage wrapping, `textwrap` filling, and the
//! `COLUMNS` / terminal width lookup from `shutil.get_terminal_size`).
//! The tables it runs on are in `cli_catalog.rs`.

use crate::cli_catalog;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Kind {
    Help,
    /// argparse's `action='version'`: print `prog version` and exit 0.
    Version,
    Store,
    StoreTrue,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Ty {
    Str,
    Int,
    Float,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Nargs {
    Zero,
    One,
    Optional,
    OneOrMore,
}

pub struct Arg {
    pub flags: &'static [&'static str],
    pub dest: &'static str,
    pub kind: Kind,
    pub ty: Ty,
    pub nargs: Nargs,
    pub choices: &'static [&'static str],
    pub help: Option<&'static str>,
    pub required: bool,
}

pub struct Command {
    pub name: &'static str,
    pub help: &'static str,
    pub args: &'static [Arg],
}

/// What the command line asked for.
pub struct Parsed {
    pub command: Option<&'static Command>,
    /// Store actions, in the order they were last set. Values are canonical
    /// text: `int` values as Python prints them, `float` values as `repr`.
    pub values: Vec<(&'static str, Vec<String>)>,
    /// store_true actions that were given.
    pub bools: Vec<&'static str>,
    /// Positional values, flattened in declaration order.
    pub positionals: Vec<String>,
}

/// The process ends here: help printed, or an argparse error.
pub struct Exit {
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

impl Parsed {
    pub fn value(&self, dest: &str) -> Option<&str> {
        self.values
            .iter()
            .find(|(name, _)| *name == dest)
            .and_then(|(_, items)| items.first())
            .map(String::as_str)
    }

    pub fn list(&self, dest: &str) -> Option<&[String]> {
        self.values
            .iter()
            .find(|(name, _)| *name == dest)
            .map(|(_, items)| items.as_slice())
    }

    pub fn flag(&self, dest: &str) -> bool {
        self.bools.iter().any(|name| *name == dest)
    }
}

// ------------------------------------------------------------------
// Parsing
// ------------------------------------------------------------------

enum Positional {
    Arg(&'static Arg),
    Subcommand,
}

struct Parser {
    prog: String,
    args: &'static [Arg],
    top: bool,
}

struct OptionTuple {
    arg: Option<&'static Arg>,
    option_string: String,
    sep: Option<String>,
    explicit: Option<String>,
}

enum Stop {
    Help,
    Version,
    Error(String),
    Exit(Exit),
}

struct State {
    values: Vec<(&'static str, Vec<String>)>,
    bools: Vec<&'static str>,
    positional_values: Vec<(&'static str, Vec<String>)>,
    command: Option<&'static Command>,
    sub_extras: Vec<String>,
}

pub fn parse(argv: &[String]) -> Result<Parsed, Exit> {
    let top = Parser {
        prog: cli_catalog::PROG.to_string(),
        args: cli_catalog::TOP_ARGS,
        top: true,
    };
    let mut state = State {
        values: Vec::new(),
        bools: Vec::new(),
        positional_values: Vec::new(),
        command: None,
        sub_extras: Vec::new(),
    };
    let mut extras = match top.parse_known(argv, &mut state) {
        Ok(extras) => extras,
        Err(Stop::Help) => {
            return Err(Exit {
                code: 0,
                stdout: format_top_help(),
                stderr: String::new(),
            })
        }
        Err(Stop::Version) => {
            return Err(Exit {
                code: 0,
                stdout: format!("{} {}\n", cli_catalog::PROG, crate::VERSION),
                stderr: String::new(),
            })
        }
        Err(Stop::Error(message)) => return Err(top.error(&message)),
        Err(Stop::Exit(exit)) => return Err(exit),
    };
    extras.append(&mut state.sub_extras);
    if !extras.is_empty() {
        return Err(top.error(&format!("unrecognized arguments: {}", extras.join(" "))));
    }
    Ok(Parsed {
        command: state.command,
        values: state.values,
        bools: state.bools,
        positionals: state
            .positional_values
            .into_iter()
            .flat_map(|(_, items)| items)
            .collect(),
    })
}

impl Parser {
    fn for_command(command: &'static Command) -> Parser {
        Parser {
            prog: format!("{} {}", cli_catalog::PROG, command.name),
            args: command.args,
            top: false,
        }
    }

    fn error(&self, message: &str) -> Exit {
        let usage = if self.top {
            format_usage(&top_usage_parts(), &self.prog)
        } else {
            let command = cli_catalog::command(self.prog.rsplit(' ').next().unwrap_or(""))
                .map(|cmd| cmd.args)
                .unwrap_or(self.args);
            format_usage(&command_usage_parts(command), &self.prog)
        };
        Exit {
            code: 2,
            stdout: String::new(),
            stderr: format!("{usage}{}: error: {message}\n", self.prog),
        }
    }

    fn help_text(&self) -> String {
        if self.top {
            format_top_help()
        } else {
            format_command_help(&self.prog, self.args)
        }
    }

    fn lookup(&self, option: &str) -> Option<&'static Arg> {
        self.args
            .iter()
            .find(|arg| arg.flags.iter().any(|flag| *flag == option))
    }

    fn option_strings(&self) -> impl Iterator<Item = (&'static Arg, &'static str)> + '_ {
        self.args
            .iter()
            .flat_map(|arg| arg.flags.iter().map(move |flag| (arg, *flag)))
    }

    fn parse_optional(&self, arg_string: &str) -> Option<Vec<OptionTuple>> {
        let mut chars = arg_string.chars();
        let first = chars.next()?;
        if first != '-' {
            return None;
        }
        if let Some(arg) = self.lookup(arg_string) {
            return Some(vec![OptionTuple {
                arg: Some(arg),
                option_string: arg_string.to_string(),
                sep: None,
                explicit: None,
            }]);
        }
        if arg_string.chars().count() == 1 {
            return None;
        }
        if let Some((option, explicit)) = arg_string.split_once('=') {
            if let Some(arg) = self.lookup(option) {
                return Some(vec![OptionTuple {
                    arg: Some(arg),
                    option_string: option.to_string(),
                    sep: Some("=".into()),
                    explicit: Some(explicit.to_string()),
                }]);
            }
        }
        let tuples = self.option_tuples(arg_string);
        if !tuples.is_empty() {
            return Some(tuples);
        }
        if looks_negative(arg_string) {
            return None;
        }
        if arg_string.contains(' ') {
            return None;
        }
        Some(vec![OptionTuple {
            arg: None,
            option_string: arg_string.to_string(),
            sep: None,
            explicit: None,
        }])
    }

    fn option_tuples(&self, option_string: &str) -> Vec<OptionTuple> {
        let mut result = Vec::new();
        let second = option_string.chars().nth(1);
        let (prefix, sep, explicit) = match option_string.split_once('=') {
            Some((prefix, explicit)) => (prefix, Some("=".to_string()), Some(explicit.to_string())),
            None => (option_string, None, None),
        };
        if second == Some('-') {
            for (arg, candidate) in self.option_strings() {
                if candidate.starts_with(prefix) {
                    result.push(OptionTuple {
                        arg: Some(arg),
                        option_string: candidate.to_string(),
                        sep: sep.clone(),
                        explicit: explicit.clone(),
                    });
                }
            }
        } else {
            let split = option_string
                .char_indices()
                .nth(2)
                .map(|(index, _)| index)
                .unwrap_or(option_string.len());
            let short_prefix = &option_string[..split];
            let short_explicit = &option_string[split..];
            for (arg, candidate) in self.option_strings() {
                if candidate == short_prefix {
                    result.push(OptionTuple {
                        arg: Some(arg),
                        option_string: candidate.to_string(),
                        sep: Some(String::new()),
                        explicit: Some(short_explicit.to_string()),
                    });
                } else if candidate.starts_with(prefix) {
                    result.push(OptionTuple {
                        arg: Some(arg),
                        option_string: candidate.to_string(),
                        sep: sep.clone(),
                        explicit: explicit.clone(),
                    });
                }
            }
        }
        result
    }

    fn positionals(&self) -> Vec<Positional> {
        let mut out: Vec<Positional> = self
            .args
            .iter()
            .filter(|arg| arg.flags.is_empty())
            .map(Positional::Arg)
            .collect();
        if self.top {
            out.push(Positional::Subcommand);
        }
        out
    }

    fn parse_known(&self, arg_strings: &[String], state: &mut State) -> Result<Vec<String>, Stop> {
        let mut option_indices: Vec<(usize, Vec<OptionTuple>)> = Vec::new();
        let mut pattern: Vec<u8> = Vec::with_capacity(arg_strings.len());
        let mut iter = arg_strings.iter().enumerate();
        while let Some((index, arg)) = iter.next() {
            if arg == "--" {
                pattern.push(b'-');
                for _ in iter.by_ref() {
                    pattern.push(b'A');
                }
                break;
            }
            match self.parse_optional(arg) {
                None => pattern.push(b'A'),
                Some(tuples) => {
                    option_indices.push((index, tuples));
                    pattern.push(b'O');
                }
            }
        }
        let mut seen: Vec<*const Arg> = Vec::new();
        let mut positionals = self.positionals();
        let mut extras: Vec<String> = Vec::new();
        let mut start_index = 0usize;
        let max_option_index = option_indices.last().map(|(index, _)| *index as isize).unwrap_or(-1);
        while (start_index as isize) <= max_option_index {
            let next_option = option_indices
                .iter()
                .map(|(index, _)| *index)
                .find(|index| *index >= start_index)
                .unwrap_or(start_index);
            if start_index != next_option {
                let end = self.consume_positionals(
                    start_index,
                    arg_strings,
                    &pattern,
                    &mut positionals,
                    &mut seen,
                    state,
                )?;
                if end > start_index {
                    start_index = end;
                    continue;
                }
                start_index = end;
            }
            if !option_indices.iter().any(|(index, _)| *index == start_index) {
                extras.extend(arg_strings[start_index..next_option].iter().cloned());
                start_index = next_option;
            }
            let tuples = &option_indices
                .iter()
                .find(|(index, _)| *index == start_index)
                .expect("option index")
                .1;
            start_index = self.consume_optional(start_index, tuples, arg_strings, &pattern, &mut extras, &mut seen, state)?;
        }
        let stop = self.consume_positionals(start_index, arg_strings, &pattern, &mut positionals, &mut seen, state)?;
        extras.extend(arg_strings[stop..].iter().cloned());
        let mut required = Vec::new();
        for arg in self.args {
            if !seen.contains(&(arg as *const Arg)) && (arg.required || is_required_positional(arg)) {
                required.push(action_name(arg));
            }
        }
        if !required.is_empty() {
            return Err(Stop::Error(format!(
                "the following arguments are required: {}",
                required.join(", ")
            )));
        }
        Ok(extras)
    }

    #[allow(clippy::too_many_arguments)]
    fn consume_optional(
        &self,
        start_index: usize,
        tuples: &[OptionTuple],
        arg_strings: &[String],
        pattern: &[u8],
        extras: &mut Vec<String>,
        seen: &mut Vec<*const Arg>,
        state: &mut State,
    ) -> Result<usize, Stop> {
        if tuples.len() > 1 {
            let options = tuples
                .iter()
                .map(|tuple| tuple.option_string.as_str())
                .collect::<Vec<_>>()
                .join(", ");
            return Err(Stop::Error(format!(
                "ambiguous option: {} could match {options}",
                arg_strings[start_index]
            )));
        }
        let first = &tuples[0];
        let mut action = first.arg;
        let mut option_string = first.option_string.clone();
        let mut sep = first.sep.clone();
        let mut explicit = first.explicit.clone();
        let mut taken: Vec<(&'static Arg, Vec<String>, String)> = Vec::new();
        let stop;
        loop {
            let Some(arg) = action else {
                extras.push(arg_strings[start_index].clone());
                return Ok(start_index + 1);
            };
            if let Some(explicit_arg) = explicit.clone() {
                let count = match_argument(arg, b"A").map_err(Stop::Error)?;
                let second = option_string.chars().nth(1);
                if count == 0 && second != Some('-') && !explicit_arg.is_empty() {
                    if sep.as_deref().is_some_and(|s| !s.is_empty()) || explicit_arg.starts_with('-') {
                        return Err(Stop::Error(format!(
                            "argument {}: ignored explicit argument {}",
                            action_name(arg),
                            py_repr(&explicit_arg)
                        )));
                    }
                    taken.push((arg, Vec::new(), option_string.clone()));
                    let mut rest = explicit_arg.chars();
                    let next_char = rest.next().expect("non-empty");
                    let candidate = format!("-{next_char}");
                    if let Some(next) = self.lookup(&candidate) {
                        action = Some(next);
                        option_string = candidate;
                        let remaining: String = rest.collect();
                        if remaining.is_empty() {
                            sep = None;
                            explicit = None;
                        } else if let Some(stripped) = remaining.strip_prefix('=') {
                            sep = Some("=".into());
                            explicit = Some(stripped.to_string());
                        } else {
                            sep = Some(String::new());
                            explicit = Some(remaining);
                        }
                    } else {
                        extras.push(format!("-{explicit_arg}"));
                        stop = start_index + 1;
                        break;
                    }
                } else if count == 1 {
                    stop = start_index + 1;
                    taken.push((arg, vec![explicit_arg], option_string.clone()));
                    break;
                } else {
                    return Err(Stop::Error(format!(
                        "argument {}: ignored explicit argument {}",
                        action_name(arg),
                        py_repr(&explicit_arg)
                    )));
                }
            } else {
                let start = start_index + 1;
                let count = match_argument(arg, &pattern[start.min(pattern.len())..]).map_err(Stop::Error)?;
                stop = start + count;
                taken.push((arg, arg_strings[start..stop].to_vec(), option_string.clone()));
                break;
            }
        }
        for (arg, values, _) in taken {
            self.take_action(arg, &values, seen, state)?;
        }
        Ok(stop)
    }

    fn consume_positionals(
        &self,
        mut start_index: usize,
        arg_strings: &[String],
        pattern: &[u8],
        positionals: &mut Vec<Positional>,
        seen: &mut Vec<*const Arg>,
        state: &mut State,
    ) -> Result<usize, Stop> {
        let selected = &pattern[start_index.min(pattern.len())..];
        let counts = match_partial(positionals, selected);
        let consumed = counts.len();
        for (index, count) in counts.into_iter().enumerate() {
            let mut values: Vec<String> = arg_strings[start_index..start_index + count].to_vec();
            match &positionals[index] {
                Positional::Subcommand => {
                    if pattern.get(start_index) == Some(&b'-') {
                        if let Some(at) = values.iter().position(|item| item == "--") {
                            values.remove(at);
                        }
                    }
                }
                Positional::Arg(_) => {
                    if pattern[start_index..start_index + count].contains(&b'-') {
                        if let Some(at) = values.iter().position(|item| item == "--") {
                            values.remove(at);
                        }
                    }
                }
            }
            start_index += count;
            match &positionals[index] {
                Positional::Arg(arg) => self.take_action(arg, &values, seen, state)?,
                Positional::Subcommand => self.take_subcommand(&values, state)?,
            }
        }
        positionals.drain(..consumed);
        Ok(start_index)
    }

    fn take_subcommand(&self, values: &[String], state: &mut State) -> Result<(), Stop> {
        let name = &values[0];
        let Some(command) = cli_catalog::command(name) else {
            let choices = cli_catalog::COMMANDS
                .iter()
                .map(|cmd| cmd.name)
                .collect::<Vec<_>>()
                .join(", ");
            return Err(Stop::Error(format!(
                "argument {}: invalid choice: {} (choose from {choices})",
                cli_catalog::SUBCOMMAND_DEST,
                py_repr(name)
            )));
        };
        state.command = Some(command);
        let sub = Parser::for_command(command);
        let mut sub_state = State {
            values: Vec::new(),
            bools: Vec::new(),
            positional_values: Vec::new(),
            command: None,
            sub_extras: Vec::new(),
        };
        match sub.parse_known(&values[1..], &mut sub_state) {
            Ok(extras) => {
                state.values = sub_state.values;
                state.bools = sub_state.bools;
                state.positional_values = sub_state.positional_values;
                state.sub_extras.extend(extras);
                Ok(())
            }
            Err(Stop::Help) => Err(Stop::Exit(Exit {
                code: 0,
                stdout: sub.help_text(),
                stderr: String::new(),
            })),
            Err(Stop::Version) => Err(Stop::Version),
            Err(Stop::Error(message)) => Err(Stop::Exit(sub.error(&message))),
            Err(Stop::Exit(exit)) => Err(Stop::Exit(exit)),
        }
    }

    fn take_action(
        &self,
        arg: &'static Arg,
        strings: &[String],
        seen: &mut Vec<*const Arg>,
        state: &mut State,
    ) -> Result<(), Stop> {
        seen.push(arg as *const Arg);
        let values = get_values(arg, strings).map_err(Stop::Error)?;
        match arg.kind {
            Kind::Help => Err(Stop::Help),
            Kind::Version => Err(Stop::Version),
            Kind::StoreTrue => {
                if !state.bools.contains(&arg.dest) {
                    state.bools.push(arg.dest);
                }
                Ok(())
            }
            Kind::Store => {
                let slot = if arg.flags.is_empty() {
                    &mut state.positional_values
                } else {
                    &mut state.values
                };
                slot.retain(|(dest, _)| *dest != arg.dest);
                if let Some(values) = values {
                    slot.push((arg.dest, values));
                }
                Ok(())
            }
        }
    }
}

fn is_required_positional(arg: &Arg) -> bool {
    arg.flags.is_empty() && matches!(arg.nargs, Nargs::One | Nargs::OneOrMore)
}

fn action_name(arg: &Arg) -> String {
    if arg.flags.is_empty() {
        arg.dest.to_string()
    } else {
        arg.flags.join("/")
    }
}

/// `None` means the namespace keeps its default (an absent `?` positional).
fn get_values(arg: &Arg, strings: &[String]) -> Result<Option<Vec<String>>, String> {
    if strings.is_empty() && arg.nargs == Nargs::Optional {
        return Ok(None);
    }
    if arg.nargs == Nargs::Zero {
        return Ok(Some(Vec::new()));
    }
    let mut out = Vec::with_capacity(strings.len());
    for text in strings {
        let value = convert(arg, text)?;
        if !arg.choices.is_empty() && !arg.choices.contains(&value.as_str()) {
            return Err(format!(
                "argument {}: invalid choice: {} (choose from {})",
                action_name(arg),
                py_repr(&value),
                arg.choices.join(", ")
            ));
        }
        out.push(value);
    }
    Ok(Some(out))
}

fn convert(arg: &Arg, text: &str) -> Result<String, String> {
    let converted = match arg.ty {
        Ty::Str => return Ok(text.to_string()),
        Ty::Int => py_int(text),
        Ty::Float => py_float(text).map(py_float_repr),
    };
    converted.ok_or_else(|| {
        let name = if arg.ty == Ty::Int { "int" } else { "float" };
        format!(
            "argument {}: invalid {name} value: {}",
            action_name(arg),
            py_repr(text)
        )
    })
}

fn match_argument(arg: &Arg, pattern: &[u8]) -> Result<usize, String> {
    let leading_a = pattern.iter().take_while(|ch| **ch == b'A').count();
    let found = match arg.nargs {
        Nargs::Zero => Some(0),
        Nargs::One => (leading_a >= 1).then_some(1),
        Nargs::Optional => Some(leading_a.min(1)),
        Nargs::OneOrMore => (leading_a >= 1).then_some(leading_a),
    };
    found.ok_or_else(|| {
        let message = match arg.nargs {
            Nargs::Optional => "expected at most one argument",
            Nargs::OneOrMore => "expected at least one argument",
            _ => "expected one argument",
        };
        format!("argument {}: {message}", action_name(arg))
    })
}

/// Lengths a positional's nargs pattern can take at `pos`, in the order
/// Python's backtracking regex engine tries them.
fn positional_lengths(positional: &Positional, pattern: &[u8], pos: usize) -> Vec<usize> {
    let rest = &pattern[pos.min(pattern.len())..];
    let dashes = rest.iter().take_while(|ch| **ch == b'-').count();
    let after_lead = &rest[dashes..];
    let has_a = after_lead.first() == Some(&b'A');
    match positional {
        Positional::Subcommand => {
            if !has_a {
                return Vec::new();
            }
            (dashes + 1..=rest.len()).rev().collect()
        }
        Positional::Arg(arg) => match arg.nargs {
            Nargs::One | Nargs::Zero => {
                if !has_a {
                    return Vec::new();
                }
                let trail = after_lead[1..].iter().take_while(|ch| **ch == b'-').count();
                (dashes + 1..=dashes + 1 + trail).rev().collect()
            }
            Nargs::OneOrMore => {
                if !has_a {
                    return Vec::new();
                }
                let more = after_lead[1..]
                    .iter()
                    .take_while(|ch| **ch == b'A' || **ch == b'-')
                    .count();
                (dashes + 1..=dashes + 1 + more).rev().collect()
            }
            Nargs::Optional => {
                let mut out = Vec::new();
                if has_a {
                    let trail = after_lead[1..].iter().take_while(|ch| **ch == b'-').count();
                    out.extend((dashes + 1..=dashes + 1 + trail).rev());
                }
                out.extend((0..=dashes).rev());
                let mut unique = Vec::new();
                for length in out {
                    if !unique.contains(&length) {
                        unique.push(length);
                    }
                }
                unique
            }
        },
    }
}

fn match_sequence(positionals: &[Positional], pattern: &[u8], pos: usize, out: &mut Vec<usize>) -> bool {
    let Some((first, rest)) = positionals.split_first() else {
        return true;
    };
    for length in positional_lengths(first, pattern, pos) {
        out.push(length);
        if match_sequence(rest, pattern, pos + length, out) {
            return true;
        }
        out.pop();
    }
    false
}

fn match_partial(positionals: &[Positional], pattern: &[u8]) -> Vec<usize> {
    for count in (1..=positionals.len()).rev() {
        let mut lengths = Vec::new();
        if match_sequence(&positionals[..count], pattern, 0, &mut lengths) {
            let end: usize = lengths.iter().sum();
            if end < pattern.len() && pattern[end] == b'O' {
                while lengths.last() == Some(&0) {
                    lengths.pop();
                }
            }
            return lengths;
        }
    }
    Vec::new()
}

/// argparse's `^-\d+$|^-\d*\.\d+$` (where `$` also matches before a final newline).
fn looks_negative(text: &str) -> bool {
    let body = text.strip_suffix('\n').unwrap_or(text);
    let Some(rest) = body.strip_prefix('-') else {
        return false;
    };
    if !rest.is_empty() && rest.chars().all(|ch| ch.is_ascii_digit()) {
        return true;
    }
    match rest.split_once('.') {
        Some((whole, frac)) => {
            whole.chars().all(|ch| ch.is_ascii_digit())
                && !frac.is_empty()
                && frac.chars().all(|ch| ch.is_ascii_digit())
        }
        None => false,
    }
}

// ------------------------------------------------------------------
// Python value conversion and repr
// ------------------------------------------------------------------

/// `str.isspace()`.
pub fn py_isspace(ch: char) -> bool {
    matches!(
        ch as u32,
        0x09..=0x0D | 0x1C..=0x20 | 0x85 | 0xA0 | 0x1680 | 0x2000..=0x200A | 0x2028 | 0x2029 | 0x202F | 0x205F | 0x3000
    )
}

fn digit_part(text: &str) -> bool {
    let bytes = text.as_bytes();
    if bytes.is_empty() || !bytes[0].is_ascii_digit() || !bytes[bytes.len() - 1].is_ascii_digit() {
        return false;
    }
    let mut previous_underscore = false;
    for byte in bytes {
        if *byte == b'_' {
            if previous_underscore {
                return false;
            }
            previous_underscore = true;
        } else if byte.is_ascii_digit() {
            previous_underscore = false;
        } else {
            return false;
        }
    }
    true
}

/// `int(text)` for base 10, as canonical decimal text.
pub fn py_int(text: &str) -> Option<String> {
    let trimmed = text.trim_matches(py_isspace);
    let (negative, digits) = match trimmed.as_bytes().first() {
        Some(b'-') => (true, &trimmed[1..]),
        Some(b'+') => (false, &trimmed[1..]),
        _ => (false, trimmed),
    };
    if !digit_part(digits) {
        return None;
    }
    let plain: String = digits.chars().filter(|ch| *ch != '_').collect();
    let stripped = plain.trim_start_matches('0');
    if stripped.is_empty() {
        return Some("0".into());
    }
    Some(if negative {
        format!("-{stripped}")
    } else {
        stripped.to_string()
    })
}

/// `float(text)`.
pub fn py_float(text: &str) -> Option<f64> {
    let trimmed = text.trim_matches(py_isspace);
    let (sign, body) = match trimmed.as_bytes().first() {
        Some(b'-') => (-1.0, &trimmed[1..]),
        Some(b'+') => (1.0, &trimmed[1..]),
        _ => (1.0, trimmed),
    };
    let lower = body.to_ascii_lowercase();
    if lower == "inf" || lower == "infinity" {
        return Some(sign * f64::INFINITY);
    }
    if lower == "nan" {
        return Some(f64::NAN);
    }
    let (mantissa, exponent) = match body.find(['e', 'E']) {
        Some(at) => (&body[..at], Some(&body[at + 1..])),
        None => (body, None),
    };
    let mantissa_ok = match mantissa.split_once('.') {
        Some((whole, frac)) => {
            (whole.is_empty() || digit_part(whole))
                && (frac.is_empty() || digit_part(frac))
                && !(whole.is_empty() && frac.is_empty())
        }
        None => digit_part(mantissa),
    };
    if !mantissa_ok {
        return None;
    }
    if let Some(exponent) = exponent {
        let digits = exponent.strip_prefix(['+', '-']).unwrap_or(exponent);
        if !digit_part(digits) {
            return None;
        }
    }
    let plain: String = body.chars().filter(|ch| *ch != '_').collect();
    plain.parse::<f64>().ok().map(|value| sign * value)
}

/// `repr(float)`.
pub fn py_float_repr(value: f64) -> String {
    if value.is_nan() {
        return "nan".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "inf".into() } else { "-inf".into() };
    }
    if value == 0.0 {
        return if value.is_sign_negative() { "-0.0".into() } else { "0.0".into() };
    }
    let (digits, exponent) = shortest_digits(value.abs());
    let sign = if value < 0.0 { "-" } else { "" };
    let decpt = exponent + 1;
    if decpt <= -4 || decpt > 16 {
        let mut out = String::from(sign);
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let exp_sign = if exponent < 0 { '-' } else { '+' };
        out.push_str(&format!("e{exp_sign}{:02}", exponent.abs()));
        return out;
    }
    let mut out = String::from(sign);
    if decpt <= 0 {
        out.push_str("0.");
        out.push_str(&"0".repeat((-decpt) as usize));
        out.push_str(&digits);
    } else if decpt as usize >= digits.len() {
        out.push_str(&digits);
        out.push_str(&"0".repeat(decpt as usize - digits.len()));
        out.push_str(".0");
    } else {
        out.push_str(&digits[..decpt as usize]);
        out.push('.');
        out.push_str(&digits[decpt as usize..]);
    }
    out
}

fn sci_parts(text: &str) -> (String, i32) {
    let (mantissa, exponent) = text.split_once('e').expect("exponent");
    (
        mantissa.chars().filter(char::is_ascii_digit).collect(),
        exponent.parse().expect("exponent digits"),
    )
}

/// The shortest digits that read back as `x`, and the decimal exponent.
/// When two shortest candidates are exactly equally close, Python's repr
/// keeps the one ending in an even digit; Rust's formatter rounds up.
fn shortest_digits(x: f64) -> (String, i32) {
    let (digits, exponent) = sci_parts(&format!("{x:e}"));
    let n = digits.len();
    let (probe, probe_exp) = sci_parts(&format!("{:.*e}", n, x));
    if !probe.ends_with('5') {
        return (digits, exponent);
    }
    // Enough digits to print x exactly: a double is m * 2^e2 and each
    // negative power of two adds at most one significant decimal digit.
    let bits = x.to_bits();
    let biased = ((bits >> 52) & 0x7ff) as i32;
    let e2 = if biased == 0 { -1074 } else { biased - 1075 };
    let precision = if e2 >= 0 { 330 } else { 17 + (-e2) as usize };
    let (exact, exact_exp) = sci_parts(&format!("{x:.precision$e}"));
    let tie = exact_exp == probe_exp
        && exact.as_bytes().get(n) == Some(&b'5')
        && exact.as_bytes()[n + 1..].iter().all(|byte| *byte == b'0');
    if !tie {
        return (digits, exponent);
    }
    let lower = &exact[..n];
    let last = lower.as_bytes()[n - 1] - b'0';
    if last % 2 == 0 {
        let text = format!("{}.{}e{}", &lower[..1], &lower[1..], exact_exp);
        if text.parse::<f64>() == Ok(x) {
            return (lower.to_string(), exact_exp);
        }
    }
    (digits, exponent)
}

fn py_printable(ch: char) -> bool {
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

/// `repr(str)`.
pub fn py_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if py_printable(c) => out.push(c),
            c if (c as u32) <= 0xFF => out.push_str(&format!("\\x{:02x}", c as u32)),
            c if (c as u32) <= 0xFFFF => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push_str(&format!("\\U{:08x}", c as u32)),
        }
    }
    out.push(quote);
    out
}

// ------------------------------------------------------------------
// Terminal width (shutil.get_terminal_size)
// ------------------------------------------------------------------

/// Columns the way `shutil.get_terminal_size().columns` reports them.
pub fn terminal_columns() -> i64 {
    if let Ok(raw) = std::env::var("COLUMNS") {
        if let Some(parsed) = py_int(&raw).and_then(|text| text.parse::<i64>().ok()) {
            if parsed > 0 {
                return parsed;
            }
        }
    }
    match stdout_columns() {
        Some(columns) if columns > 0 => columns,
        _ => 80,
    }
}

#[cfg(windows)]
fn stdout_columns() -> Option<i64> {
    #[repr(C)]
    struct Coord {
        x: i16,
        y: i16,
    }
    #[repr(C)]
    struct SmallRect {
        left: i16,
        top: i16,
        right: i16,
        bottom: i16,
    }
    #[repr(C)]
    struct ScreenBufferInfo {
        size: Coord,
        cursor: Coord,
        attributes: u16,
        window: SmallRect,
        max_size: Coord,
    }
    unsafe extern "system" {
        fn GetStdHandle(which: u32) -> *mut std::ffi::c_void;
        fn GetConsoleScreenBufferInfo(handle: *mut std::ffi::c_void, info: *mut ScreenBufferInfo) -> i32;
    }
    const STD_OUTPUT_HANDLE: u32 = -11i32 as u32;
    let handle = unsafe { GetStdHandle(STD_OUTPUT_HANDLE) };
    if handle.is_null() || handle as isize == -1 {
        return None;
    }
    let mut info = ScreenBufferInfo {
        size: Coord { x: 0, y: 0 },
        cursor: Coord { x: 0, y: 0 },
        attributes: 0,
        window: SmallRect { left: 0, top: 0, right: 0, bottom: 0 },
        max_size: Coord { x: 0, y: 0 },
    };
    if unsafe { GetConsoleScreenBufferInfo(handle, &mut info) } == 0 {
        return None;
    }
    let _ = (&info.size, &info.cursor, info.attributes, info.window.top, info.window.bottom, &info.max_size);
    let _ = (info.size.x, info.size.y, info.cursor.x, info.cursor.y, info.max_size.x, info.max_size.y);
    Some(i64::from(info.window.right) - i64::from(info.window.left) + 1)
}

#[cfg(unix)]
fn stdout_columns() -> Option<i64> {
    #[repr(C)]
    struct WinSize {
        rows: u16,
        cols: u16,
        xpixel: u16,
        ypixel: u16,
    }
    #[cfg(any(target_os = "macos", target_os = "ios", target_os = "freebsd", target_os = "netbsd", target_os = "openbsd", target_os = "dragonfly"))]
    const TIOCGWINSZ: std::ffi::c_ulong = 0x4008_7468;
    #[cfg(not(any(target_os = "macos", target_os = "ios", target_os = "freebsd", target_os = "netbsd", target_os = "openbsd", target_os = "dragonfly")))]
    const TIOCGWINSZ: std::ffi::c_ulong = 0x5413;
    unsafe extern "C" {
        fn ioctl(fd: i32, request: std::ffi::c_ulong, ...) -> i32;
    }
    let mut size = WinSize { rows: 0, cols: 0, xpixel: 0, ypixel: 0 };
    if unsafe { ioctl(1, TIOCGWINSZ, &mut size as *mut WinSize) } != 0 {
        return None;
    }
    let _ = (size.rows, size.xpixel, size.ypixel);
    Some(i64::from(size.cols))
}

#[cfg(not(any(unix, windows)))]
fn stdout_columns() -> Option<i64> {
    None
}

// ------------------------------------------------------------------
// HelpFormatter
// ------------------------------------------------------------------

struct Layout {
    width: i64,
    max_help_position: i64,
}

impl Layout {
    fn current() -> Layout {
        let width = terminal_columns() - 2;
        Layout {
            width,
            max_help_position: 24.min((width - 20).max(4)),
        }
    }
}

/// One entry in the help body (an action or a sub-command line).
struct Entry {
    invocation: String,
    help: Option<String>,
    indent: i64,
    subentries: Vec<Entry>,
}

fn metavar(arg: &Arg) -> String {
    if !arg.choices.is_empty() {
        format!("{{{}}}", arg.choices.join(","))
    } else if arg.flags.is_empty() {
        arg.dest.to_string()
    } else {
        arg.dest.to_uppercase()
    }
}

fn format_args(arg: &Arg) -> String {
    let name = metavar(arg);
    match arg.nargs {
        Nargs::Zero | Nargs::One => name,
        Nargs::Optional => format!("[{name}]"),
        Nargs::OneOrMore => format!("{name} [{name} ...]"),
    }
}

fn invocation(arg: &Arg) -> String {
    if arg.flags.is_empty() {
        return metavar(arg);
    }
    if arg.nargs == Nargs::Zero {
        return arg.flags.join(", ");
    }
    let args = format_args(arg);
    arg.flags
        .iter()
        .map(|flag| format!("{flag} {args}"))
        .collect::<Vec<_>>()
        .join(", ")
}

fn usage_part(arg: &Arg) -> String {
    if arg.flags.is_empty() {
        return format_args(arg);
    }
    let part = if arg.nargs == Nargs::Zero {
        arg.flags[0].to_string()
    } else {
        format!("{} {}", arg.flags[0], format_args(arg))
    };
    if arg.required {
        part
    } else {
        format!("[{part}]")
    }
}

struct UsageParts {
    optionals: Vec<String>,
    positionals: Vec<String>,
}

fn command_usage_parts(args: &[Arg]) -> UsageParts {
    UsageParts {
        optionals: args.iter().filter(|arg| !arg.flags.is_empty()).map(usage_part).collect(),
        positionals: args.iter().filter(|arg| arg.flags.is_empty()).map(usage_part).collect(),
    }
}

fn subcommand_metavar() -> String {
    format!(
        "{{{}}}",
        cli_catalog::COMMANDS
            .iter()
            .map(|cmd| cmd.name)
            .collect::<Vec<_>>()
            .join(",")
    )
}

fn top_usage_parts() -> UsageParts {
    let mut parts = command_usage_parts(cli_catalog::TOP_ARGS);
    parts.positionals.push(format!("{} ...", subcommand_metavar()));
    parts
}

/// argparse's cleanup of group brackets on the joined usage text.
fn clean_usage(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut step1 = String::new();
    let mut index = 0;
    while index < chars.len() {
        let ch = chars[index];
        step1.push(ch);
        if (ch == '[' || ch == '(') && chars.get(index + 1) == Some(&' ') {
            index += 2;
            continue;
        }
        index += 1;
    }
    let chars: Vec<char> = step1.chars().collect();
    let mut step2 = String::new();
    let mut index = 0;
    while index < chars.len() {
        if chars[index] == ' ' && matches!(chars.get(index + 1), Some(']') | Some(')')) {
            index += 1;
            continue;
        }
        step2.push(chars[index]);
        index += 1;
    }
    let chars: Vec<char> = step2.chars().collect();
    let mut step3 = String::new();
    let mut index = 0;
    while index < chars.len() {
        if chars[index] == '[' || chars[index] == '(' {
            let mut probe = index + 1;
            while chars.get(probe) == Some(&' ') {
                probe += 1;
            }
            if matches!(chars.get(probe), Some(']') | Some(')')) {
                index = probe + 1;
                continue;
            }
        }
        step3.push(chars[index]);
        index += 1;
    }
    step3.trim().to_string()
}

/// `re.findall(r'\(.*?\)+(?=\s|$)|\[.*?\]+(?=\s|$)|\S+', text)`.
fn usage_split(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let boundary = |at: usize| at >= chars.len() || chars[at].is_whitespace() || (at + 1 == chars.len() && chars[at] == '\n');
    let mut out = Vec::new();
    let mut index = 0;
    while index < chars.len() {
        let ch = chars[index];
        let mut matched = None;
        if ch == '(' || ch == '[' {
            let close = if ch == '(' { ')' } else { ']' };
            let mut lazy = index + 1;
            'outer: while lazy < chars.len() {
                if chars[lazy] == '\n' {
                    break;
                }
                if chars[lazy] == close {
                    let run = chars[lazy..].iter().take_while(|c| **c == close).count();
                    for take in (1..=run).rev() {
                        if boundary(lazy + take) {
                            matched = Some(lazy + take);
                            break 'outer;
                        }
                    }
                }
                lazy += 1;
            }
        }
        if matched.is_none() && !ch.is_whitespace() {
            let mut end = index;
            while end < chars.len() && !chars[end].is_whitespace() {
                end += 1;
            }
            matched = Some(end);
        }
        match matched {
            Some(end) => {
                out.push(chars[index..end].iter().collect());
                index = end;
            }
            None => index += 1,
        }
    }
    out
}

fn char_len(text: &str) -> i64 {
    text.chars().count() as i64
}

fn get_lines(parts: &[String], indent: &str, prefix: Option<&str>, text_width: i64) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    let mut line: Vec<&str> = Vec::new();
    let indent_length = char_len(indent);
    let mut line_len = match prefix {
        Some(prefix) => char_len(prefix) - 1,
        None => indent_length - 1,
    };
    for part in parts {
        if line_len + 1 + char_len(part) > text_width && !line.is_empty() {
            lines.push(format!("{indent}{}", line.join(" ")));
            line.clear();
            line_len = indent_length - 1;
        }
        line.push(part);
        line_len += char_len(part) + 1;
    }
    if !line.is_empty() {
        lines.push(format!("{indent}{}", line.join(" ")));
    }
    if prefix.is_some() && !lines.is_empty() {
        lines[0] = lines[0].chars().skip(indent_length as usize).collect();
    }
    lines
}

/// `HelpFormatter._format_usage`, returning `usage: ...\n\n`.
fn usage_block(parts: &UsageParts, prog: &str, layout: &Layout) -> String {
    let prefix = "usage: ";
    let opt_usage = clean_usage(&parts.optionals.join(" "));
    let pos_usage = clean_usage(&parts.positionals.join(" "));
    let all: Vec<String> = parts.optionals.iter().chain(parts.positionals.iter()).cloned().collect();
    let action_usage = clean_usage(&all.join(" "));
    let mut usage = [prog.to_string(), action_usage]
        .into_iter()
        .filter(|item| !item.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    let text_width = layout.width;
    if char_len(prefix) + char_len(&usage) > text_width {
        let opt_parts = usage_split(&opt_usage);
        let pos_parts = usage_split(&pos_usage);
        let lines: Vec<String> = if (char_len(prefix) + char_len(prog)) as f64 <= 0.75 * text_width as f64 {
            let indent = " ".repeat((char_len(prefix) + char_len(prog) + 1) as usize);
            if !opt_parts.is_empty() {
                let mut first = vec![prog.to_string()];
                first.extend(opt_parts.iter().cloned());
                let mut lines = get_lines(&first, &indent, Some(prefix), text_width);
                lines.extend(get_lines(&pos_parts, &indent, None, text_width));
                lines
            } else if !pos_parts.is_empty() {
                let mut first = vec![prog.to_string()];
                first.extend(pos_parts.iter().cloned());
                get_lines(&first, &indent, Some(prefix), text_width)
            } else {
                vec![prog.to_string()]
            }
        } else {
            let indent = " ".repeat(char_len(prefix) as usize);
            let all_parts: Vec<String> = opt_parts.iter().chain(pos_parts.iter()).cloned().collect();
            let mut lines = get_lines(&all_parts, &indent, None, text_width);
            if lines.len() > 1 {
                lines = Vec::new();
                lines.extend(get_lines(&opt_parts, &indent, None, text_width));
                lines.extend(get_lines(&pos_parts, &indent, None, text_width));
            }
            let mut out = vec![prog.to_string()];
            out.extend(lines);
            out
        };
        usage = lines.join("\n");
    }
    format!("{prefix}{usage}\n\n")
}

/// `parser.format_usage()`: what `parser.error` prints before the message.
fn format_usage(parts: &UsageParts, prog: &str) -> String {
    finish(usage_block(parts, prog, &Layout::current()))
}

fn finish(raw: String) -> String {
    let mut collapsed = String::with_capacity(raw.len());
    let mut newlines = 0;
    for ch in raw.chars() {
        if ch == '\n' {
            newlines += 1;
            continue;
        }
        if newlines > 0 {
            collapsed.push_str(if newlines >= 3 { "\n\n" } else if newlines == 2 { "\n\n" } else { "\n" });
            newlines = 0;
        }
        collapsed.push(ch);
    }
    let trimmed = collapsed.trim_matches('\n');
    if trimmed.is_empty() {
        return String::new();
    }
    format!("{trimmed}\n")
}

fn entries_for(args: &[Arg], positional: bool, indent: i64) -> Vec<Entry> {
    args.iter()
        .filter(|arg| arg.flags.is_empty() == positional)
        .map(|arg| Entry {
            invocation: invocation(arg),
            help: arg.help.map(str::to_string),
            indent,
            subentries: Vec::new(),
        })
        .collect()
}

fn max_length(entries: &[Entry]) -> i64 {
    entries
        .iter()
        .map(|entry| {
            let own = char_len(&entry.invocation) + entry.indent;
            own.max(max_length(&entry.subentries))
        })
        .max()
        .unwrap_or(0)
}

fn format_entry(entry: &Entry, layout: &Layout, action_max_length: i64, out: &mut String) {
    let help_position = (action_max_length + 2).min(layout.max_help_position);
    let help_width = (layout.width - help_position).max(11);
    let action_width = help_position - entry.indent - 2;
    let indent = " ".repeat(entry.indent.max(0) as usize);
    let header_len = char_len(&entry.invocation);
    let has_help = entry.help.as_deref().is_some_and(|text| !text.is_empty());
    let mut indent_first = 0;
    if !has_help {
        out.push_str(&format!("{indent}{}\n", entry.invocation));
    } else if header_len <= action_width {
        let pad = " ".repeat((action_width - header_len).max(0) as usize);
        out.push_str(&format!("{indent}{}{pad}  ", entry.invocation));
    } else {
        out.push_str(&format!("{indent}{}\n", entry.invocation));
        indent_first = help_position;
    }
    if let Some(help) = entry.help.as_deref().filter(|text| !text.trim_matches(py_isspace).is_empty()) {
        let lines = wrap(&collapse_ascii_whitespace(help), help_width);
        if let Some((first, rest)) = lines.split_first() {
            out.push_str(&" ".repeat(indent_first.max(0) as usize));
            out.push_str(first);
            out.push('\n');
            for line in rest {
                out.push_str(&" ".repeat(help_position.max(0) as usize));
                out.push_str(line);
                out.push('\n');
            }
        }
    } else if !out.ends_with('\n') {
        out.push('\n');
    }
    for sub in &entry.subentries {
        format_entry(sub, layout, action_max_length, out);
    }
}

fn section(heading: &str, entries: &[Entry], layout: &Layout, action_max_length: i64) -> String {
    if entries.is_empty() {
        return String::new();
    }
    let mut body = String::new();
    for entry in entries {
        format_entry(entry, layout, action_max_length, &mut body);
    }
    format!("\n{heading}:\n{body}\n")
}

pub fn format_command_help(prog: &str, args: &[Arg]) -> String {
    let layout = Layout::current();
    let positionals = entries_for(args, true, 2);
    let optionals = entries_for(args, false, 2);
    let action_max_length = max_length(&positionals).max(max_length(&optionals));
    let mut raw = String::from("\n");
    raw.push_str(&usage_block(&command_usage_parts(args), prog, &layout));
    raw.push_str(&section("positional arguments", &positionals, &layout, action_max_length));
    raw.push_str(&section("options", &optionals, &layout, action_max_length));
    raw.push('\n');
    finish(raw)
}

pub fn format_top_help() -> String {
    let layout = Layout::current();
    let subentries: Vec<Entry> = cli_catalog::COMMANDS
        .iter()
        .map(|cmd| Entry {
            invocation: cmd.name.to_string(),
            help: Some(cmd.help.to_string()),
            indent: 4,
            subentries: Vec::new(),
        })
        .collect();
    let positionals = vec![Entry {
        invocation: subcommand_metavar(),
        help: None,
        indent: 2,
        subentries,
    }];
    let optionals = entries_for(cli_catalog::TOP_ARGS, false, 2);
    let action_max_length = max_length(&positionals).max(max_length(&optionals));
    let mut raw = String::from("\n");
    raw.push_str(&usage_block(&top_usage_parts(), cli_catalog::PROG, &layout));
    let text_width = layout.width.max(11);
    raw.push_str(&wrap(&collapse_ascii_whitespace(cli_catalog::DESCRIPTION), text_width).join("\n"));
    raw.push_str("\n\n");
    raw.push_str(&section("positional arguments", &positionals, &layout, action_max_length));
    raw.push_str(&section("options", &optionals, &layout, action_max_length));
    raw.push('\n');
    finish(raw)
}

fn collapse_ascii_whitespace(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut in_space = false;
    for ch in text.chars() {
        if matches!(ch, ' ' | '\t' | '\n' | '\r' | '\x0b' | '\x0c') {
            in_space = true;
            continue;
        }
        if in_space && !out.is_empty() {
            out.push(' ');
        }
        in_space = false;
        out.push(ch);
    }
    out
}

// ------------------------------------------------------------------
// textwrap.wrap (break_on_hyphens, break_long_words, drop_whitespace)
// ------------------------------------------------------------------

fn tw_space(ch: char) -> bool {
    matches!(ch, '\t' | '\n' | '\x0b' | '\x0c' | '\r' | ' ')
}

fn tw_word(ch: char) -> bool {
    ch.is_alphanumeric() || ch == '_'
}

fn tw_letter(ch: char) -> bool {
    tw_word(ch) && !ch.is_ascii_digit() && !ch.is_numeric()
}

fn tw_word_punct(ch: char) -> bool {
    tw_word(ch) || matches!(ch, '!' | '"' | '\'' | '&' | '.' | ',' | '?')
}

/// `TextWrapper.wordsep_re.split` with empty pieces dropped.
fn tw_chunks(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let at = |i: usize| chars.get(i).copied();
    let mut out: Vec<String> = Vec::new();
    let mut pending_start = 0usize;
    let mut pos = 0usize;
    while pos < n {
        let end = if tw_space(chars[pos]) {
            let mut end = pos;
            while end < n && tw_space(chars[end]) {
                end += 1;
            }
            Some(end)
        } else if pos > 0
            && tw_word_punct(chars[pos - 1])
            && chars[pos] == '-'
            && at(pos + 1) == Some('-')
            && {
                let mut dash_end = pos;
                while dash_end < n && chars[dash_end] == '-' {
                    dash_end += 1;
                }
                at(dash_end).is_some_and(tw_word)
            }
        {
            // em-dash between words: -{2,} greedy, then (?=\w); backtracking
            // never needs fewer dashes because the run must be followed by \w.
            let mut dash_end = pos;
            while dash_end < n && chars[dash_end] == '-' {
                dash_end += 1;
            }
            Some(dash_end)
        } else {
            let mut found = None;
            let mut stop = pos + 1;
            while stop <= n {
                if tw_space(chars[stop - 1]) {
                    break;
                }
                // hyphenated word: the next char is '-' and the hyphen rule holds
                if at(stop) == Some('-') {
                    let behind_two = stop >= 2 && tw_letter(chars[stop - 1]) && tw_letter(chars[stop - 2]);
                    let behind_four = stop >= 3
                        && tw_letter(chars[stop - 1])
                        && chars[stop - 2] == '-'
                        && tw_letter(chars[stop - 3]);
                    let ahead = match (at(stop + 1), at(stop + 2), at(stop + 3)) {
                        (Some(a), Some('-'), Some(b)) if tw_letter(a) && tw_letter(b) => true,
                        (Some(a), Some(b), _) if tw_letter(a) && tw_letter(b) => true,
                        _ => false,
                    };
                    if (behind_two || behind_four) && ahead {
                        found = Some(stop + 1);
                        break;
                    }
                }
                // end of word
                if stop == n || tw_space(chars[stop]) {
                    found = Some(stop);
                    break;
                }
                // em-dash follows
                if tw_word_punct(chars[stop - 1]) && at(stop) == Some('-') && at(stop + 1) == Some('-') {
                    let mut dash_end = stop;
                    while dash_end < n && chars[dash_end] == '-' {
                        dash_end += 1;
                    }
                    if at(dash_end).is_some_and(tw_word) {
                        found = Some(stop);
                        break;
                    }
                }
                stop += 1;
            }
            found
        };
        match end {
            Some(end) if end > pos => {
                if pending_start < pos {
                    out.push(chars[pending_start..pos].iter().collect());
                }
                out.push(chars[pos..end].iter().collect());
                pos = end;
                pending_start = end;
            }
            _ => pos += 1,
        }
    }
    if pending_start < n {
        out.push(chars[pending_start..n].iter().collect());
    }
    out.retain(|chunk| !chunk.is_empty());
    out
}

fn is_blank(chunk: &str) -> bool {
    chunk.chars().all(py_isspace)
}

/// `textwrap.wrap(text, width)` with the TextWrapper defaults argparse uses.
pub fn wrap(text: &str, width: i64) -> Vec<String> {
    let munged: String = text
        .chars()
        .flat_map(|ch| {
            if ch == '\t' {
                vec![' '; 8]
            } else if tw_space(ch) {
                vec![' ']
            } else {
                vec![ch]
            }
        })
        .collect();
    let mut chunks = tw_chunks(&munged);
    chunks.reverse();
    let mut lines: Vec<String> = Vec::new();
    while !chunks.is_empty() {
        let mut cur_line: Vec<String> = Vec::new();
        let mut cur_len: i64 = 0;
        if chunks.last().is_some_and(|chunk| is_blank(chunk)) && !lines.is_empty() {
            chunks.pop();
        }
        while let Some(chunk) = chunks.last() {
            let len = char_len(chunk);
            if cur_len + len <= width {
                cur_len += len;
                cur_line.push(chunks.pop().expect("chunk"));
            } else {
                break;
            }
        }
        if chunks.last().is_some_and(|chunk| char_len(chunk) > width) {
            let space_left = if width < 1 { 1 } else { width - cur_len };
            let chunk = chunks.last().expect("chunk").clone();
            let chunk_chars: Vec<char> = chunk.chars().collect();
            let mut end = space_left.max(0) as usize;
            if chunk_chars.len() as i64 > space_left {
                let limit = end.min(chunk_chars.len());
                if let Some(hyphen) = chunk_chars[..limit].iter().rposition(|ch| *ch == '-') {
                    if hyphen > 0 && chunk_chars[..hyphen].iter().any(|ch| *ch != '-') {
                        end = hyphen + 1;
                    }
                }
            }
            let end = end.min(chunk_chars.len());
            cur_line.push(chunk_chars[..end].iter().collect());
            let last = chunks.len() - 1;
            chunks[last] = chunk_chars[end..].iter().collect();
            if chunks[last].is_empty() {
                // an empty chunk stays in Python's list too; it is popped by the
                // inner loop on the next pass at zero cost
            }
            cur_len = cur_line.iter().map(|item| char_len(item)).sum();
        }
        if cur_line.last().is_some_and(|chunk| is_blank(chunk)) {
            cur_len -= char_len(cur_line.last().expect("chunk"));
            cur_line.pop();
        }
        let _ = cur_len;
        if !cur_line.is_empty() {
            lines.push(cur_line.concat());
        }
    }
    lines
}
