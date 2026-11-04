//! Background reap for long-running servers.

use crate::act_identity;
use crate::act_lock;
use crate::act_view;
use crate::engine::Engine;
use crate::util;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

pub fn spawn(engine: Arc<Mutex<Engine>>) {
    let flag = std::env::var("COORDINATIONHUB_HOUSEKEEPING").unwrap_or_else(|_| "1".into());
    if matches!(flag.as_str(), "0" | "false" | "no" | "off" | "") {
        return;
    }
    let interval = util::env_f64("COORDINATIONHUB_HOUSEKEEPING_AGENTS_INTERVAL_S", 30.0);
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs_f64(interval.max(1.0)));
        let Ok(mut engine) = engine.lock() else {
            return;
        };
        if engine.store.begin().is_err() {
            continue;
        }
        let stale = util::env_f64("COORDINATIONHUB_AGENTS_STALE_TIMEOUT_S", 600.0);
        let _ = act_identity::reap_stale(&mut engine.store.state, stale);
        let _ = act_lock::reap_expired(&mut engine.store.state, 0.0);
        let _ = act_view::who(
            &mut engine.store.state,
            "status",
            None,
            None,
            None,
            600.0,
            None,
        );
        let _ = engine.store.commit();
        engine.store.end();
    });
}
