//! CoordinationHub: live coordination and work ownership for coding agents.

pub mod act_board;
pub mod act_identity;
pub mod act_lock;
pub mod act_view;
pub mod cli;
pub mod db;
pub mod engine;
pub mod http;
pub mod json;
pub mod mcp;
pub mod util;
pub mod validate;

pub use engine::Engine;
pub use json::Value;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
