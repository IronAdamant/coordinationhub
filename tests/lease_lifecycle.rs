//! The coordinator lease after its holder leaves or its TTL runs out
//! (B12 from the Specula live test, 1.1.1).

mod common;

use common::{tool_text, Hub};
use coordinationhub::json::{self, Value};
use std::time::Duration;

fn reply(result: &Value) -> Value {
    let text = tool_text(result).0;
    json::parse(&text).unwrap_or_else(|err| panic!("{err}: {text}"))
}

fn acquire(agent: &str, ttl: Value) -> (&'static str, Value) {
    ("acquire_coordinator_lease", json::obj(vec![("agent_id", json::s(agent)), ("ttl", ttl)]))
}

fn lease(action: &str, agent: Option<&str>) -> (&'static str, Value) {
    let mut pairs = vec![("action", json::s(action))];
    if let Some(agent) = agent {
        pairs.push(("agent_id", json::s(agent)));
    }
    ("manage_leases", json::obj(pairs))
}

fn holder(value: &Value) -> Option<String> {
    value
        .get("leader")
        .and_then(|leader| leader.get("holder_id"))
        .and_then(Value::as_str)
        .map(str::to_string)
}

#[test]
fn deregistering_the_holder_releases_the_lease() {
    let hub = Hub::new("leasedereg");
    let replies = hub.mcp(&[
        ("register_agent", json::obj(vec![("agent_id", json::s("lease-probe"))])),
        acquire("lease-probe", json::i(300)),
        lease("get", None),
        ("deregister_agent", json::obj(vec![("agent_id", json::s("lease-probe"))])),
        lease("get", None),
        acquire("next", json::i(30)),
        ("deregister_agent", json::obj(vec![("agent_id", json::s("bystander"))])),
        lease("get", None),
    ]);
    assert_eq!(holder(&reply(&replies[2])).as_deref(), Some("lease-probe"));
    let gone = reply(&replies[3]);
    assert_eq!(gone.get("lease_released"), Some(&json::b(true)), "{}", tool_text(&replies[3]).0);
    assert_eq!(reply(&replies[4]).get("leader"), Some(&Value::Null));
    assert_eq!(reply(&replies[5]).get("acquired"), Some(&json::b(true)));
    // Another agent leaving does not touch the lease.
    assert_eq!(reply(&replies[6]).get("lease_released"), Some(&json::b(false)));
    assert_eq!(holder(&reply(&replies[7])).as_deref(), Some("next"));
}

#[test]
fn an_expired_lease_reads_as_no_leader() {
    let hub = Hub::new("leaseexpiry");
    let first = hub.mcp(&[
        acquire("lead", json::f(0.2)),
        acquire("live", json::i(30)),
        lease("get", None),
    ]);
    assert_eq!(reply(&first[0]).get("acquired"), Some(&json::b(true)));
    // While the lease is live, another agent is refused and get shows it.
    assert_eq!(reply(&first[1]).get("acquired"), Some(&json::b(false)));
    assert_eq!(holder(&reply(&first[2])).as_deref(), Some("lead"));
    std::thread::sleep(Duration::from_millis(400));

    // The CLI views of the leader agree.
    let get = hub.run(&["get-leader"]);
    assert_eq!((get.code, get.stdout.as_str()), (0, "No active coordinator leader\n"));
    let status = hub.run(&["leader-status"]);
    assert_eq!(status.stdout, "No active leader\n");
    let dashboard = hub.run(&["ha-dashboard"]);
    assert_eq!(dashboard.stdout, "No active coordinator lease\n");
    assert_eq!(hub.json(&["get-leader"]).get("leader"), Some(&Value::Null));

    // The old holder cannot revive it; it has to acquire like anyone else.
    let refresh = hub.run(&["refresh-coordinator-lease", "lead"]);
    assert_eq!(refresh.code, 4);
    assert!(
        refresh.stderr.starts_with("Refresh failed: Lease expired at ")
            && refresh.stderr.contains("call acquire_coordinator_lease to take it again"),
        "{}",
        refresh.stderr
    );
    let replies = hub.mcp(&[
        lease("get", Some("observer")),
        lease("refresh", Some("lead")),
        acquire("next", json::i(30)),
        lease("get", None),
        lease("refresh", Some("next")),
    ]);
    assert_eq!(reply(&replies[0]).get("leader"), Some(&Value::Null));
    assert_eq!(reply(&replies[1]).get("refreshed"), Some(&json::b(false)));
    assert_eq!(reply(&replies[2]).get("acquired"), Some(&json::b(true)));
    assert_eq!(holder(&reply(&replies[3])).as_deref(), Some("next"));
    // A live lease still refreshes.
    assert_eq!(reply(&replies[4]).get("refreshed"), Some(&json::b(true)));
}

#[test]
fn claim_takes_an_expired_lease() {
    let hub = Hub::new("leaseclaim");
    let first = hub.mcp(&[acquire("lead", json::f(0.2))]);
    assert_eq!(reply(&first[0]).get("acquired"), Some(&json::b(true)));
    std::thread::sleep(Duration::from_millis(400));
    let replies = hub.mcp(&[
        ("manage_leases", json::obj(vec![("action", json::s("claim")), ("agent_id", json::s("heir")), ("ttl", json::i(30))])),
        lease("get", None),
    ]);
    assert_eq!(reply(&replies[0]).get("claimed"), Some(&json::b(true)));
    assert_eq!(holder(&reply(&replies[1])).as_deref(), Some("heir"));
}
