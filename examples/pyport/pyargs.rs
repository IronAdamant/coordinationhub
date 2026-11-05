//! CPython 3.12 `argparse` for a flat parser of optionals (`-h`,
//! `store_true` flags, single-value options) with a description and no
//! positionals: option classification with unique-prefix abbreviation,
//! `--opt=value`, `-xyz` short clusters, `--`, the error sentences, and the
//! help/usage text (formatted by the crate's `HelpFormatter` port, so
//! `COLUMNS` and the terminal width wrap it as Python would).

use coordinationhub::argparse::{format_command_help, terminal_columns, wrap, Arg, Kind, Nargs};

use super::pyio::{exit, py_isspace, py_repr, write_err, write_out};

pub struct Parser {
    pub prog: &'static str,
    pub description: &'static str,
    pub args: &'static [Arg],
}

#[derive(Default)]
pub struct Parsed {
    flags: Vec<&'static str>,
    values: Vec<(&'static str, String)>,
}

impl Parsed {
    pub fn flag(&self, dest: &str) -> bool {
        self.flags.contains(&dest)
    }

    pub fn value(&self, dest: &str) -> Option<&str> {
        self.values.iter().rev().find(|(d, _)| *d == dest).map(|(_, v)| v.as_str())
    }
}

struct OptionTuple {
    arg: Option<&'static Arg>,
    option_string: String,
    sep: Option<String>,
    explicit: Option<String>,
}

enum Stop {
    Help,
    Error(String),
}

impl Parser {
    fn help_text(&self) -> String {
        let body = format_command_help(self.prog, self.args);
        let (usage, rest) = body.split_once("\n\n").unwrap_or((body.as_str(), ""));
        let collapsed = self
            .description
            .split(|c: char| c.is_ascii_whitespace())
            .filter(|part| !part.is_empty())
            .collect::<Vec<_>>()
            .join(" ");
        let width = (terminal_columns() - 2).max(11);
        let description = wrap(collapsed.trim_matches(py_isspace), width).join("\n");
        format!("{usage}\n\n{description}\n\n{rest}")
    }

    fn usage(&self) -> String {
        let body = format_command_help(self.prog, self.args);
        let usage = body.split_once("\n\n").map(|(u, _)| u).unwrap_or(body.as_str());
        format!("{usage}\n")
    }

    fn error(&self, message: &str) -> ! {
        write_err(&self.usage());
        write_err(&format!("{}: error: {message}\n", self.prog));
        exit(2)
    }

    fn lookup(&self, option: &str) -> Option<&'static Arg> {
        self.args.iter().find(|arg| arg.flags.contains(&option))
    }

    fn option_strings(&self) -> impl Iterator<Item = (&'static Arg, &'static str)> + '_ {
        self.args.iter().flat_map(|arg| arg.flags.iter().map(move |flag| (arg, *flag)))
    }

    /// `parser.parse_args(argv)`: help and errors end the process.
    pub fn parse_args(&self, argv: &[String]) -> Parsed {
        let mut parsed = Parsed::default();
        match self.parse_known(argv, &mut parsed) {
            Ok(extras) if extras.is_empty() => parsed,
            Ok(extras) => self.error(&format!("unrecognized arguments: {}", extras.join(" "))),
            Err(Stop::Help) => {
                write_out(&self.help_text());
                exit(0)
            }
            Err(Stop::Error(message)) => self.error(&message),
        }
    }

    fn parse_optional(&self, arg_string: &str) -> Option<Vec<OptionTuple>> {
        if !arg_string.starts_with('-') {
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
        if looks_negative(arg_string) || arg_string.contains(' ') {
            return None;
        }
        Some(vec![OptionTuple { arg: None, option_string: arg_string.to_string(), sep: None, explicit: None }])
    }

    fn option_tuples(&self, option_string: &str) -> Vec<OptionTuple> {
        let mut result = Vec::new();
        if option_string.chars().nth(1) == Some('-') {
            let (prefix, sep, explicit) = match option_string.split_once('=') {
                Some((prefix, explicit)) => (prefix, Some("=".to_string()), Some(explicit.to_string())),
                None => (option_string, None, None),
            };
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
            let split = option_string.char_indices().nth(2).map(|(i, _)| i).unwrap_or(option_string.len());
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
                } else if candidate.starts_with(option_string) {
                    result.push(OptionTuple {
                        arg: Some(arg),
                        option_string: candidate.to_string(),
                        sep: None,
                        explicit: None,
                    });
                }
            }
        }
        result
    }

    fn parse_known(&self, arg_strings: &[String], parsed: &mut Parsed) -> Result<Vec<String>, Stop> {
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
        let mut extras: Vec<String> = Vec::new();
        let mut start_index = 0usize;
        let max_option_index = option_indices.last().map(|(i, _)| *i as isize).unwrap_or(-1);
        while (start_index as isize) <= max_option_index {
            let next_option = option_indices
                .iter()
                .map(|(i, _)| *i)
                .find(|i| *i >= start_index)
                .unwrap_or(start_index);
            if !option_indices.iter().any(|(i, _)| *i == start_index) {
                extras.extend(arg_strings[start_index..next_option].iter().cloned());
                start_index = next_option;
            }
            let tuples = &option_indices.iter().find(|(i, _)| *i == start_index).expect("option index").1;
            start_index = self.consume_optional(start_index, tuples, arg_strings, &pattern, &mut extras, parsed)?;
        }
        extras.extend(arg_strings[start_index.min(arg_strings.len())..].iter().cloned());
        Ok(extras)
    }

    fn consume_optional(
        &self,
        start_index: usize,
        tuples: &[OptionTuple],
        arg_strings: &[String],
        pattern: &[u8],
        extras: &mut Vec<String>,
        parsed: &mut Parsed,
    ) -> Result<usize, Stop> {
        if tuples.len() > 1 {
            let options = tuples.iter().map(|t| t.option_string.as_str()).collect::<Vec<_>>().join(", ");
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
        let mut taken: Vec<(&'static Arg, Vec<String>)> = Vec::new();
        let stop;
        loop {
            let Some(arg) = action else {
                extras.push(arg_strings[start_index].clone());
                return Ok(start_index + 1);
            };
            if let Some(explicit_arg) = explicit.clone() {
                let count = match_argument(arg, b"A")?;
                let second = option_string.chars().nth(1);
                if count == 0 && second != Some('-') && !explicit_arg.is_empty() {
                    if sep.as_deref().is_some_and(|s| !s.is_empty()) || explicit_arg.starts_with('-') {
                        return Err(Stop::Error(format!(
                            "argument {}: ignored explicit argument {}",
                            action_name(arg),
                            py_repr(&explicit_arg)
                        )));
                    }
                    taken.push((arg, Vec::new()));
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
                    taken.push((arg, vec![explicit_arg]));
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
                let count = match_argument(arg, &pattern[start.min(pattern.len())..])?;
                stop = start + count;
                taken.push((arg, arg_strings[start..stop].to_vec()));
                break;
            }
        }
        for (arg, values) in taken {
            match arg.kind {
                Kind::Help => return Err(Stop::Help),
                // The ported doc tools declare no version flag.
                Kind::Version => {
                    return Err(Stop::Error(format!("argument {}: not supported", action_name(arg))))
                }
                Kind::StoreTrue => {
                    if !parsed.flags.contains(&arg.dest) {
                        parsed.flags.push(arg.dest);
                    }
                }
                Kind::Store => {
                    if let Some(value) = values.into_iter().next() {
                        parsed.values.push((arg.dest, value));
                    }
                }
            }
        }
        Ok(stop)
    }
}

fn action_name(arg: &Arg) -> String {
    arg.flags.join("/")
}

fn match_argument(arg: &Arg, pattern: &[u8]) -> Result<usize, Stop> {
    match arg.nargs {
        Nargs::Zero => Ok(0),
        _ if pattern.first() == Some(&b'A') => Ok(1),
        _ => Err(Stop::Error(format!("argument {}: expected one argument", action_name(arg)))),
    }
}

/// argparse's `^-\d+$|^-\d*\.\d+$` (`$` also matches before a final newline).
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
            whole.chars().all(|ch| ch.is_ascii_digit()) && !frac.is_empty() && frac.chars().all(|ch| ch.is_ascii_digit())
        }
        None => false,
    }
}
