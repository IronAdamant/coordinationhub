//! Std-only port of `scripts/propose_stale_phrases.py`: propose new
//! `STALE_PHRASES` entries for files deleted under `coordinationhub/`.
//!
//! ```text
//! cargo run --release --example propose_stale_phrases -- [--root DIR] [--since REV]
//! ```
//!
//! Walks `git log REV..HEAD --diff-filter=D` (REV defaults to the last commit
//! whose diff of `scripts/gen_docs.py` touched `STALE_PHRASES`), and for each
//! deleted `.py` file proposes its basename plus every public top-level
//! `def`/`class`/assigned name of its pre-deletion revision (`git show
//! SHA^:PATH`). Output, flags and exit codes follow the Python script;
//! `--root` (default: this crate's directory) is the checkout it ran in.

#[allow(dead_code)]
mod pyport;

use std::path::Path;

use coordinationhub::argparse::{Arg, Kind, Nargs, Ty};

use pyport::pyargs::Parser;
use pyport::pyast::top_level_names;
use pyport::pyio::{self, print_err, print_out, py_splitlines, py_strip};

const ARGS: &[Arg] = &[
    Arg {
        flags: &["-h", "--help"],
        dest: "help",
        kind: Kind::Help,
        ty: Ty::Str,
        nargs: Nargs::Zero,
        choices: &[],
        help: Some("show this help message and exit"),
        required: false,
    },
    Arg {
        flags: &["--since"],
        dest: "since",
        kind: Kind::Store,
        ty: Ty::Str,
        nargs: Nargs::One,
        choices: &[],
        help: Some(
            "Git rev to walk from (default: the last commit that touched STALE_PHRASES in scripts/gen_docs.py).",
        ),
        required: false,
    },
];

const PARSER: Parser = Parser {
    prog: "propose_stale_phrases.py",
    description: "Propose STALE_PHRASES entries for deleted files.",
    args: ARGS,
};

/// `_run`: stdout of a git command, or `""` on a non-zero exit (`None` if
/// the output could not be decoded, see `pyio::run_text`).
fn run(root: &Path, argv: &[String]) -> Option<String> {
    let result = pyio::run_text(argv, root);
    if result.returncode == 0 {
        result.stdout
    } else {
        Some(String::new())
    }
}

fn argv(items: &[&str]) -> Vec<String> {
    items.iter().map(|s| s.to_string()).collect()
}

fn last_stale_phrases_commit(root: &Path) -> Option<String> {
    // str(GEN_DOCS_PATH.relative_to(REPO_ROOT)): OS separator.
    let rel = pyio::join_os(&["scripts".to_string(), "gen_docs.py".to_string()]);
    let out = run(root, &argv(&["git", "log", "-S", "STALE_PHRASES", "--pretty=format:%H", "-n", "1", "--", &rel]))
        .unwrap_or_else(|| pyio::none_attr("strip"));
    let sha = py_strip(&out);
    (!sha.is_empty()).then(|| sha.to_string())
}

fn deleted_files_since(root: &Path, rev: &str) -> Vec<(String, String)> {
    let range = format!("{rev}..HEAD");
    let out = run(
        root,
        &argv(&["git", "log", &range, "--diff-filter=D", "--name-only", "--pretty=format:COMMIT %H", "--", "coordinationhub/"]),
    )
    .unwrap_or_else(|| pyio::none_attr("splitlines"));
    let mut pairs = Vec::new();
    let mut cur_sha = String::new();
    for line in py_splitlines(&out) {
        if line.starts_with("COMMIT ") {
            cur_sha = py_strip(line.split_once(' ').map(|(_, rest)| rest).unwrap_or("")).to_string();
            continue;
        }
        let path = py_strip(line);
        if !path.is_empty() && path.ends_with(".py") {
            pairs.push((cur_sha.clone(), path.to_string()));
        }
    }
    pairs
}

/// `Path(path).name`.
fn basename(path: &str) -> String {
    let is_sep = |c: char| c == '/' || (cfg!(windows) && c == '\\');
    path.rsplit(is_sep).next().unwrap_or(path).to_string()
}

fn short(text: &str) -> String {
    text.chars().take(7).collect()
}

fn propose_entries(root: &Path, since_rev: &str) -> Vec<(String, String)> {
    let mut proposals: Vec<(String, String)> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for (sha, path) in deleted_files_since(root, since_rev) {
        let base = basename(&path);
        if !seen.contains(&base) {
            proposals.push((base.clone(), format!("deleted in commit {} — file no longer exists", short(&sha))));
            seen.push(base.clone());
        }
        let source = run(root, &argv(&["git", "show", &format!("{sha}^:{path}")])).unwrap_or_default();
        if source.is_empty() {
            continue;
        }
        for name in top_level_names(&source) {
            if seen.contains(&name) {
                continue;
            }
            proposals.push((name.clone(), format!("removed with {base} in commit {}", short(&sha))));
            seen.push(name);
        }
    }
    proposals
}

fn main() {
    let (root, args) = pyport::take_root(std::env::args().skip(1).collect());
    let args = PARSER.parse_args(&args);

    let since_rev = match args.value("since") {
        Some(rev) => Some(rev.to_string()),
        None => last_stale_phrases_commit(&root),
    };
    let since_rev = match since_rev {
        Some(rev) if !rev.is_empty() => rev,
        _ => {
            print_err("Could not determine baseline. Pass --since explicitly.");
            pyio::exit(1);
        }
    };

    let proposals = propose_entries(&root, &since_rev);
    if proposals.is_empty() {
        print_out(&format!("No new deletions under coordinationhub/ since {}.", short(&since_rev)));
        pyio::exit(0);
    }

    print_out(&format!("# Proposed STALE_PHRASES entries (baseline: {}):", short(&since_rev)));
    print_out("# Review and paste into STALE_PHRASES in scripts/gen_docs.py.");
    print_out("");
    for (phrase, reason) in proposals {
        let safe = phrase.replace('"', "\\\"");
        print_out(&format!("    \"{safe}\": \"{reason}\","));
    }
    pyio::exit(0);
}
