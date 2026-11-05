//! Shared, std-only support for the ports of the inherited Python dev
//! scripts (`examples/gen_docs.rs`, `examples/propose_stale_phrases.rs`,
//! `examples/update_dispatch_floor.rs`).
//!
//! The scripts ran on CPython; their observable behaviour depends on
//! CPython's text-mode I/O (newline translation, locale codecs), on
//! `argparse`, on `json`, and on `ast`. Each submodule reproduces exactly
//! the slice of that behaviour the three scripts use:
//!
//! * [`pyio`]: stdout/stderr/file text I/O, codecs, uncaught-exception exits.
//! * [`pytok`]: a Python tokenizer and logical-line/block structure.
//! * [`pyast`]: the AST questions the scripts ask (docstrings, top-level
//!   names, dict-literal keys, call sites, module constants).
//! * [`pyjson`]: `json.loads` (with its error messages) and `json.dumps`.
//! * [`pyargs`]: the `argparse` paths of a flat parser of flags.

pub mod pyargs;
pub mod pyast;
pub mod pyio;
pub mod pyjson;
pub mod pytok;

use std::path::PathBuf;

/// Split `--root DIR` / `--root=DIR` out of the command line (it is the one
/// flag the ports add) and return `(root, remaining_args)`. Without it the
/// root is this crate's directory, as the Python scripts used the checkout
/// they lived in (`Path(__file__).resolve().parent.parent`).
pub fn take_root(args: Vec<String>) -> (PathBuf, Vec<String>) {
    let mut root: Option<String> = None;
    let mut rest = Vec::new();
    let mut iter = args.into_iter();
    while let Some(arg) = iter.next() {
        if arg == "--root" {
            match iter.next() {
                Some(value) => root = Some(value),
                None => {
                    pyio::write_err("--root requires a directory argument\n");
                    pyio::exit(2);
                }
            }
        } else if let Some(value) = arg.strip_prefix("--root=") {
            root = Some(value.to_string());
        } else {
            rest.push(arg);
        }
    }
    let root = PathBuf::from(root.unwrap_or_else(|| env!("CARGO_MANIFEST_DIR").to_string()));
    (pyio::resolve(&root), rest)
}
