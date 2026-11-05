//! CoordinationHub: live coordination and work ownership for coding agents.

pub mod act_board;
pub mod act_identity;
pub mod act_lock;
pub mod act_view;
pub mod argparse;
pub mod cli;
pub mod cli_catalog;
pub mod cli_cmds;
pub mod cli_text;
pub mod db;
pub mod engine;
pub mod groups;
pub mod hooks;
pub mod house;
pub mod http;
pub mod json;
pub mod mcp;
pub mod util;
pub mod validate;

pub use engine::Engine;
pub use json::Value;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
