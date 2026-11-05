//! Per-command help, matching argparse's text for the 0.8.0 commands.

pub fn command_help(command: &str) -> Option<&'static str> {
    crate::cli_catalog::command(command).map(|cmd| cmd.help)
}
