//! CoordinationHub: live coordination and work ownership for coding agents.
//! Standard library only.

pub mod call;
pub mod cli;
pub mod call_flow;
pub mod call_id;
pub mod call_lease;
pub mod call_msg;
pub mod call_vis;
pub mod call_lock;
pub mod hash;
pub mod hook;
pub mod http_srv;
pub mod hub;
pub mod mcp;
pub mod json;
pub mod model;
pub mod paths;
pub mod store;
pub mod util;
pub mod validate;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub use hub::Hub;
pub use json::Json;
