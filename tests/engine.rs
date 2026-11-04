use coordinationhub::{Hub, Json};
use std::sync::Arc;

fn hub() -> Hub {
    let ns = format!("hub-test-{}-{}", std::process::id(), line_salt());
    let dir = std::env::temp_dir().join(&ns);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    Hub::open(Some(&dir.join("store")), Some(&dir), "hub").unwrap()
}

fn line_salt() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos() as u64
}

fn obj(pairs: Vec<(&str, Json)>) -> Json {
    Json::object(pairs)
}

#[test]
fn register_lock_and_claim() {
    let hub = hub();
    let reg = hub
        .call(
            "register_agent",
            &obj(vec![("agent_id", Json::String("a".into()))]),
        )
        .unwrap();
    assert_eq!(reg.get("agent_id").and_then(|v| v.as_str()), Some("a"));
    let lock = hub
        .call(
            "acquire_lock",
            &obj(vec![
                ("document_path", Json::String("src/a.py".into())),
                ("agent_id", Json::String("a".into())),
            ]),
        )
        .unwrap();
    assert_eq!(lock.get("acquired").and_then(|v| v.as_bool()), Some(true));
    let claim = hub
        .call(
            "claim_work",
            &obj(vec![
                ("agent_id", Json::String("a".into())),
                ("area", Json::String("src/".into())),
            ]),
        )
        .unwrap();
    assert_eq!(claim.get("claimed").and_then(|v| v.as_bool()), Some(true));
    let compact = hub
        .call(
            "compact_status",
            &obj(vec![("agent_id", Json::String("a".into()))]),
        )
        .unwrap();
    assert_eq!(compact.get("unread").and_then(|v| v.as_i64()), Some(0));
}

#[test]
fn contended_lock_allows_one_holder() {
    let hub = Arc::new(hub());
    hub.call(
        "register_agent",
        &obj(vec![("agent_id", Json::String("a".into()))]),
    )
    .unwrap();
    hub.call(
        "register_agent",
        &obj(vec![("agent_id", Json::String("b".into()))]),
    )
    .unwrap();
    let h1 = Arc::clone(&hub);
    let h2 = Arc::clone(&hub);
    let t1 = std::thread::spawn(move || {
        h1.call(
            "acquire_lock",
            &obj(vec![
                ("document_path", Json::String("solo.py".into())),
                ("agent_id", Json::String("a".into())),
            ]),
        )
        .unwrap()
    });
    let t2 = std::thread::spawn(move || {
        h2.call(
            "acquire_lock",
            &obj(vec![
                ("document_path", Json::String("solo.py".into())),
                ("agent_id", Json::String("b".into())),
            ]),
        )
        .unwrap()
    });
    let r1 = t1.join().unwrap();
    let r2 = t2.join().unwrap();
    let wins = [r1, r2]
        .iter()
        .filter(|r| r.get("acquired").and_then(|v| v.as_bool()) == Some(true))
        .count();
    assert_eq!(wins, 1);
}

#[test]
fn manifest_has_no_crates() {
    let text = include_str!("../Cargo.toml");
    let deps = text
        .split("[dependencies]")
        .nth(1)
        .unwrap()
        .split("[dev-dependencies]")
        .next()
        .unwrap();
    assert!(
        deps.lines().all(|l| l.trim().is_empty() || l.trim().starts_with('#')),
        "Cargo.toml [dependencies] must stay empty: {deps:?}"
    );
}

#[test]
fn sha256_empty() {
    assert_eq!(
        coordinationhub::hash::sha256_hex(b""),
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
}
