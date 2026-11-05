//! Checks for the fifth review: one test per fix. Inputs are not taken
//! from the review reports. Where 0.8.0 had a bug, the test pins the
//! corrected result that MIGRATION.md describes.

mod common;

use common::{tool_text, Hub};
use coordinationhub::json::{self, Value};

fn lines(text: &str) -> Vec<&str> {
    text.lines().collect()
}

#[test]
fn cli_lease_ttl_is_a_float() {
    let hub = Hub::new("ttl");
    hub.run(&["register", "marimba"]);
    hub.run(&["register", "gamelan"]);
    let got = hub.json(&["acquire-coordinator-lease", "marimba", "--ttl", "23"]);
    assert_eq!(got.get("ttl"), Some(&Value::Float(23.0)));
    assert!(json::dumps_pretty(&got).contains("\"ttl\": 23.0"));
    hub.run(&["release-coordinator-lease", "marimba"]);
    let claimed = hub.json(&["claim-leadership", "gamelan", "--ttl", "31"]);
    assert_eq!(claimed.get("ttl"), Some(&Value::Float(31.0)));
    hub.run(&["release-coordinator-lease", "gamelan"]);
    let default = hub.json(&["acquire-coordinator-lease", "marimba"]);
    assert_eq!(default.get("ttl"), Some(&Value::Float(10.0)));
}

#[test]
fn mcp_lease_ttl_keeps_the_callers_number() {
    let hub = Hub::new("mcpttl");
    let replies = hub.mcp(&[
        ("register_agent", json::obj(vec![("agent_id", json::s("marimba"))])),
        (
            "acquire_coordinator_lease",
            json::obj(vec![("agent_id", json::s("marimba")), ("ttl", json::i(40))]),
        ),
        (
            "manage_leases",
            json::obj(vec![("action", json::s("release")), ("agent_id", json::s("marimba"))]),
        ),
        ("acquire_coordinator_lease", json::obj(vec![("agent_id", json::s("marimba"))])),
    ]);
    assert!(tool_text(&replies[1]).0.contains("\"ttl\":40,"));
    assert!(tool_text(&replies[3]).0.contains("\"ttl\":10.0,"));
}

fn conflicts_setup(hub: &Hub) {
    hub.run(&["register", "oud"]);
    hub.run(&["register", "lyra"]);
    hub.run(&["acquire-lock", "sheet/a.md", "oud"]);
    hub.run(&["acquire-lock", "sheet/a.md", "lyra"]);
    hub.run(&["acquire-lock", "sheet/a.md", "lyra"]);
    hub.run(&["acquire-lock", "sheet/b.md", "lyra"]);
    hub.run(&["acquire-lock", "sheet/b.md", "oud"]);
}

#[test]
fn negative_limit_returns_every_row() {
    let hub = Hub::new("neglimit");
    conflicts_setup(&hub);
    let conflicts = hub.json(&["get-conflicts", "--limit", "-1"]);
    assert_eq!(conflicts.get("conflicts").and_then(Value::as_array).map(|a| a.len()), Some(3));
    let none = hub.json(&["get-conflicts", "--limit", "0"]);
    assert_eq!(none.get("conflicts").and_then(Value::as_array).map(|a| a.len()), Some(0));
    let hot = hub.json(&["contention-hotspots", "--limit", "-1"]);
    assert_eq!(hot.get("hotspots").and_then(Value::as_array).map(|a| a.len()), Some(2));
    let notes = hub.json(&["get-notifications", "--limit=-2"]);
    assert!(notes.get("notifications").and_then(Value::as_array).map(|a| a.len()).unwrap_or(0) >= 2);
}

#[test]
fn negative_or_tiny_wait_timeout_times_out_at_once() {
    let hub = Hub::new("negwait");
    hub.run(&["register", "oud"]);
    hub.run(&["notify-change", "sheet/c.md", "modified", "oud"]);
    for timeout in ["-1", "0", "0.05"] {
        let started = std::time::Instant::now();
        let got = hub.json(&["wait-for-notifications", "oud", "--timeout", timeout]);
        assert_eq!(got.get("timed_out"), Some(&Value::Bool(true)), "timeout {timeout}");
        assert_eq!(got.get("notifications").and_then(Value::as_array).map(|a| a.len()), Some(0));
        assert!(started.elapsed().as_secs_f64() < 2.0);
    }
}

#[test]
fn negative_max_entries_prunes_every_row() {
    let hub = Hub::new("prune");
    conflicts_setup(&hub);
    hub.run(&["notify-change", "sheet/c.md", "modified", "oud"]);
    std::thread::sleep(std::time::Duration::from_millis(1100));
    let pruned = hub.json(&["prune-notifications", "--max-entries", "-1"]);
    assert!(pruned.get("pruned").and_then(Value::as_i64).unwrap() > 0);
    let again = hub.json(&["prune-notifications", "--max-entries", "0"]);
    assert_eq!(again.get("pruned").and_then(Value::as_i64), Some(0));
}

#[test]
fn handoff_messages_carry_handoff_type() {
    let hub = Hub::new("handoff");
    hub.run(&["register", "oud"]);
    hub.run(&["register", "lyra"]);
    hub.run(&["broadcast", "oud", "--handoff-targets", "lyra", "--document-path", "sheet/coda.md"]);
    let messages = hub.json(&["get-messages", "lyra"]);
    let payload = messages
        .get("messages")
        .and_then(Value::as_array)
        .and_then(|items| items.first())
        .and_then(|item| item.get("payload"))
        .cloned()
        .unwrap();
    assert_eq!(payload.get("handoff_type").and_then(Value::as_str), Some("scope_transfer"));
    assert_eq!(payload.get("document_path").and_then(Value::as_str), Some("sheet/coda.md"));
}

#[test]
fn refused_lease_and_claim_show_holder_and_expiry() {
    let hub = Hub::new("refused");
    hub.run(&["register", "oud"]);
    hub.run(&["register", "lyra"]);
    hub.run(&["acquire-coordinator-lease", "oud", "--ttl", "50"]);
    let out = hub.run(&["acquire-coordinator-lease", "lyra", "--ttl", "5"]);
    let got = lines(&out.stdout);
    assert_eq!(got[0], "Lease acquisition failed \u{2014} leadership held by another agent");
    assert_eq!(got[1], "  Current holder: oud");
    assert!(got[2].starts_with("  Expires at: "), "{}", out.stdout);
    let claim = hub.run(&["claim-leadership", "lyra"]);
    let got = lines(&claim.stdout);
    assert_eq!(got[0], "Leadership claim failed");
    assert_eq!(got[1], "  Current holder: oud");
    assert!(got[2].starts_with("  Expires at: "));
    hub.run(&["release-coordinator-lease", "oud"]);
    let ok = hub.run(&["claim-leadership", "lyra", "--ttl", "12"]);
    let got = lines(&ok.stdout);
    assert_eq!(got[0], "Leadership claimed: COORDINATOR_LEADER");
    assert_eq!(got[1], "  Holder: lyra");
    assert_eq!(got[2], "  TTL: 12.0s");
}

#[test]
fn readable_mode_reports_what_happened() {
    let hub = Hub::new("prose");
    std::fs::create_dir_all(hub.project.join("sheet")).unwrap();
    std::fs::write(hub.project.join("sheet/a.py"), "x = 1\n").unwrap();
    std::fs::write(hub.project.join("sheet/b.md"), "# b\n").unwrap();
    hub.run(&["register", "oud"]);
    hub.run(&["create-task", "PIECE-1", "oud", "tune strings"]);
    hub.run(&["update-task-status", "PIECE-1", "completed"]);
    let waited = hub.run(&["wait-for-task", "PIECE-1", "--timeout", "0.3"]);
    assert_eq!(waited.stdout, "Task completed: PIECE-1\n");
    hub.run(&["create-task", "PIECE-2", "oud", "oil keys"]);
    hub.run(&["update-task-status", "PIECE-2", "failed", "--error", "sticky key"]);
    let failed = hub.run(&["wait-for-task", "PIECE-2", "--timeout", "0.3"]);
    assert_eq!(failed.stdout, "Task failed: PIECE-2\n");
    let history = hub.run(&["task-failure-history", "PIECE-2"]);
    let got = lines(&history.stdout);
    assert_eq!(got[0], "Failure history for PIECE-2 (1 entries):");
    assert!(got[1].starts_with("  attempt 1 at ") && got[1].ends_with(": sticky key"), "{}", got[1]);
    let scan = hub.run(&["scan-project"]);
    assert_eq!(lines(&scan.stdout)[0], "Scanned 2 file(s)");
    hub.run(&["spawn-subagent", "oud", "copyist"]);
    let reported = hub.run(&["report-subagent-spawned", "oud", "oud/copy-1", "--subagent-type", "copyist"]);
    assert!(reported.stdout.contains("  Description: (none)\n"), "{}", reported.stdout);
}

#[test]
fn six_former_crash_commands_print_their_rows() {
    let hub = Hub::new("crashfix");
    conflicts_setup(&hub);
    let conflicts = hub.run(&["get-conflicts"]);
    assert_eq!(conflicts.code, 0);
    assert_eq!(lines(&conflicts.stdout)[0], "Conflicts (3):");
    assert!(conflicts.stdout.contains("  sheet/a.md: lyra denied; held by oud\n"));
    hub.run(&["create-task", "PIECE-1", "oud", "tune strings"]);
    hub.run(&["create-subtask", "PIECE-1.a", "PIECE-1", "oud", "string a"]);
    let available = hub.run(&["get-available-tasks"]);
    assert_eq!(available.code, 0);
    assert!(available.stdout.contains("  PIECE-1 \u{2014} tune strings\n"), "{}", available.stdout);
    let all = hub.run(&["query-tasks", "all"]);
    assert_eq!(all.code, 0);
    assert!(all.stdout.contains("  [pending] PIECE-1 \u{2014} (unassigned)\n    tune strings\n"));
    let single = hub.run(&["query-tasks", "task", "--task-id", "PIECE-1"]);
    assert_eq!(lines(&single.stdout)[0], "Tasks (1):");
    let tree = hub.run(&["query-tasks", "tree", "--root-task-id", "PIECE-1"]);
    assert!(tree.stdout.contains("    [pending] PIECE-1.a"), "{}", tree.stdout);
    hub.run(&["acquire-coordinator-lease", "oud", "--ttl", "30"]);
    let leader = hub.run(&["get-leader"]);
    assert_eq!(leader.code, 0);
    assert_eq!(lines(&leader.stdout)[0], "Current leader: oud");
    let status = hub.run(&["leader-status"]);
    assert!(status.stdout.starts_with("Leader: oud (expires "));
    hub.run(&["spawn-subagent", "oud", "copyist"]);
    let spawns = hub.run(&["list-pending-spawns", "oud"]);
    assert_eq!(spawns.code, 0);
    assert_eq!(lines(&spawns.stdout)[1], "  oud.copyist.0");
    assert!(lines(&spawns.stdout)[2].starts_with("    type=copyist | age="));
}

#[test]
fn duplicate_task_id_is_a_clear_error() {
    let hub = Hub::new("dup");
    hub.run(&["register", "oud"]);
    hub.run(&["create-task", "PIECE-9", "oud", "first"]);
    let prose = hub.run(&["create-task", "PIECE-9", "oud", "second"]);
    assert_eq!(prose.code, 1);
    assert!(prose.stderr.starts_with("Error: task PIECE-9 already exists; "), "{}", prose.stderr);
    let as_json = hub.run(&["create-task", "PIECE-9", "oud", "third", "--json"]);
    assert_eq!(as_json.code, 1);
    let parsed = json::parse(&as_json.stdout).unwrap();
    assert!(parsed.get("error").and_then(Value::as_str).unwrap().starts_with("task PIECE-9 already exists"));
    let replies = hub.mcp(&[(
        "create_task",
        json::obj(vec![
            ("task_id", json::s("PIECE-9")),
            ("parent_agent_id", json::s("oud")),
            ("description", json::s("again")),
        ]),
    )]);
    let (text, is_error) = tool_text(&replies[0]);
    assert!(is_error);
    assert!(text.starts_with("Error: task PIECE-9 already exists; "), "{text}");
}

#[test]
fn mcp_unknown_tool_and_integral_priority() {
    let hub = Hub::new("mcperr");
    let replies = hub.mcp(&[
        ("register_agent", json::obj(vec![("agent_id", json::s("oud"))])),
        ("no_such_tool_q", json::obj(vec![])),
        (
            "create_task",
            json::obj(vec![
                ("task_id", json::s("P-3")),
                ("parent_agent_id", json::s("oud")),
                ("description", json::s("d")),
                ("priority", json::f(3.0)),
            ]),
        ),
        (
            "create_task",
            json::obj(vec![
                ("task_id", json::s("P-4")),
                ("parent_agent_id", json::s("oud")),
                ("description", json::s("d")),
                ("priority", json::f(3.5)),
            ]),
        ),
    ]);
    let (text, is_error) = tool_text(&replies[1]);
    assert!(is_error);
    assert!(text.starts_with("Error: Unknown tool: 'no_such_tool_q'. "), "{text}");
    let (text, is_error) = tool_text(&replies[2]);
    assert!(!is_error, "{text}");
    assert!(text.contains("\"priority\":3"));
    let (text, is_error) = tool_text(&replies[3]);
    assert!(is_error);
    assert!(text.starts_with("Input validation error:"), "{text}");
}

#[test]
fn caller_worktree_root_is_stored_as_given() {
    let hub = Hub::new("worktree");
    let replies = hub.mcp(&[
        (
            "register_agent",
            json::obj(vec![("agent_id", json::s("oud")), ("worktree_root", json::s("rel-tree"))]),
        ),
        ("list_agents", json::obj(vec![("active_only", json::b(false))])),
    ]);
    assert!(tool_text(&replies[1]).0.contains("\"worktree_root\":\"rel-tree\""));
}

#[test]
fn help_lists_every_flag_with_its_description() {
    // argparse wraps help text and may break after a hyphen, so compare
    // with whitespace removed.
    let squash = |text: &str| text.split_whitespace().collect::<String>();
    for command in coordinationhub::cli_catalog::COMMANDS {
        let prog = format!("coordinationhub {}", command.name);
        let help = squash(&coordinationhub::argparse::format_command_help(&prog, command.args));
        for arg in command.args {
            for flag in arg.flags {
                assert!(help.contains(flag), "{} --help lacks {flag}", command.name);
            }
            if arg.flags.is_empty() {
                let shown = if arg.choices.is_empty() {
                    arg.dest.to_string()
                } else {
                    format!("{{{}}}", arg.choices.join(","))
                };
                assert!(help.contains(&shown), "{} --help lacks {shown}", command.name);
            }
            if let Some(text) = arg.help {
                assert!(help.contains(&squash(text)), "{} --help lacks: {text}", command.name);
            }
        }
    }
    let top = squash(&coordinationhub::argparse::format_top_help());
    for command in coordinationhub::cli_catalog::COMMANDS {
        assert!(top.contains(&squash(command.help)), "top --help lacks {}", command.name);
    }
}

#[test]
fn doctor_ends_with_a_summary_line() {
    let hub = Hub::new("doctor");
    hub.run(&["register", "oud"]);
    let out = hub.run(&["doctor"]);
    assert_eq!(out.code, 0);
    let last = lines(&out.stdout).last().copied().unwrap();
    assert!(
        last == "All checks passed." || last.starts_with("Some checks failed."),
        "{}",
        out.stdout
    );
}

#[test]
fn paths_inside_the_project_stay_relative_before_the_file_exists() {
    let hub = Hub::new("paths");
    let norm = coordinationhub::util::normalize_path(
        &hub.project.join("sub").join("new.rs").display().to_string(),
        Some(&hub.project),
    );
    assert_eq!(norm, "sub/new.rs");
    let root = coordinationhub::util::path_str(&hub.project);
    assert!(!root.starts_with(r"\\?\") && !root.starts_with("//?/"), "{root}");
}

#[test]
fn python_number_text() {
    use coordinationhub::argparse::{py_float, py_float_repr, py_int};
    assert_eq!(py_float_repr(6.151199340820312e-05), "6.151199340820312e-05");
    assert_eq!(py_float_repr(29.0), "29.0");
    assert_eq!(py_float_repr(1e16), "1e+16");
    assert_eq!(py_float_repr(1234567890123456.0), "1234567890123456.0");
    assert_eq!(py_float_repr(0.0001), "0.0001");
    assert_eq!(py_float_repr(-0.0), "-0.0");
    assert_eq!(py_int(" +1_000 "), Some("1000".to_string()));
    assert_eq!(py_int("1__0"), None);
    assert_eq!(py_float("1e1"), Some(10.0));
    assert_eq!(py_float(".5"), Some(0.5));
    assert_eq!(py_float("-Infinity"), Some(f64::NEG_INFINITY));
    assert_eq!(py_float("ten"), None);
    assert_eq!(json::dumps(&json::f(6.151199340820312e-05)), "6.151199340820312e-05");
}
