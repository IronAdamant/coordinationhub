//! Human-readable CLI text, matching the Python command printers.

use crate::json::Value;

pub fn show(command: &str, value: &Value, pos: &[String]) {
    let id = pos.first().map(|s| s.as_str()).unwrap_or("");
    match command {
        "status" => render_status(value),
        "register" => {
            println!("Registered: {}", text(value, "agent_id"));
            if let Some(parent) = value.get("parent_id").and_then(Value::as_str) {
                println!("  Parent: {parent}");
            }
            println!("  Worktree: {}", text(value, "worktree_root"));
        }
        "heartbeat" => {
            let updated = value.get("updated").and_then(Value::as_bool).unwrap_or(false);
            println!("Heartbeat: {id} — updated: {}", py_bool(updated));
        }
        "deregister" => {
            println!("Deregistered: {id}");
            println!("  Children orphaned: {}", num(value, "children_orphaned"));
            println!("  Locks released: {}", num(value, "locks_released"));
        }
        "list-agents" => render_agents(value),
        "acquire-lock" | "release-lock" | "refresh-lock" => render_lock(command, value, id),
        "lock-status" => {
            if value.get("locked").and_then(Value::as_bool) == Some(true) {
                println!("LOCKED: {id}");
                println!("  By: {}", text(value, "locked_by"));
                println!("  Expires: {}", raw(value, "expires_at"));
            } else {
                println!("UNLOCKED: {id}");
            }
        }
        "list-locks" => render_lock_list(value),
        "admin-locks" => println!("{}", crate::json::dumps_pretty(value)),
        "notify-change" => println!(
            "Recorded: {} on {} by {}",
            pos.get(1).map(|s| s.as_str()).unwrap_or(""),
            id,
            pos.get(2).map(|s| s.as_str()).unwrap_or("")
        ),
        "get-notifications" | "prune-notifications" => {
            if value.get("pruned").is_some() && command == "prune-notifications" {
                println!("Pruned {} notification(s)", num(value, "pruned"));
            } else {
                render_notes(value);
            }
        }
        "wait-for-notifications" => {
            let notes = value.get("notifications").and_then(Value::as_array);
            let count = notes.map(|items| items.len()).unwrap_or(0);
            println!("Notifications for {id}: {count}");
            if let Some(items) = notes {
                for item in items {
                    println!(
                        "  {}: {} by {}",
                        text(item, "document_path"),
                        text(item, "change_type"),
                        text(item, "agent_id")
                    );
                }
            }
        }
        "broadcast" => {
            if value.get("handoff_id").is_some() {
                println!("HANDOFF from {id}: {}", raw(value, "handoff_id"));
                println!("  To: {}", join_strings(value, "to_agents"));
                let doc = value.get("document_path").and_then(Value::as_str).unwrap_or("");
                println!(
                    "  Document: {}",
                    if doc.is_empty() { "(none)" } else { doc }
                );
                println!("  Type: {}", text(value, "handoff_type"));
            } else {
                println!("Broadcast from {id}");
            }
        }
        "get-handoffs" => render_handoffs(value),
        "complete-handoff" => println!("Completed handoff {id}"),
        "cancel-handoff" => println!("Cancelled handoff {id}"),
        "acknowledge-handoff" => {
            if value.get("acknowledged").and_then(Value::as_bool) == Some(true) {
                println!("Acknowledged handoff {id}");
            } else {
                println!("Failed: {}", text(value, "reason"));
            }
        }
        "create-task" => {
            println!("Task created: {id}");
            if num(value, "priority") != 0 {
                println!("  Priority: {}", num(value, "priority"));
            }
        }
        "create-subtask" => {
            println!("Subtask created: {id}");
            println!(
                "  Parent task: {}",
                pos.get(1).map(|s| s.as_str()).unwrap_or("")
            );
        }
        "assign-task" => println!(
            "Task assigned: {id} → {}",
            pos.get(1).map(|s| s.as_str()).unwrap_or("")
        ),
        "update-task-status" => println!(
            "Task updated: {id} → {}",
            pos.get(1).map(|s| s.as_str()).unwrap_or("")
        ),
        "query-tasks" => render_tasks(value),
        "get-available-tasks" => render_available(value),
        "declare-work-intent" => {
            println!(
                "Intent declared: {id} → {}",
                pos.get(1).map(|s| s.as_str()).unwrap_or("")
            );
            println!(
                "  Intent: {}",
                pos.get(2).map(|s| s.as_str()).unwrap_or("")
            );
        }
        "get-work-intents" => render_intents(value),
        "clear-work-intent" => println!("Intent cleared: {id}"),
        "declare-dependency" => println!(
            "Dependency declared: {id} → {}",
            pos.get(1).map(|s| s.as_str()).unwrap_or("")
        ),
        "satisfy-dependency" => println!("Dependency {id} marked as satisfied"),
        "get-leader" => render_leader(value, true),
        "leader-status" => render_leader(value, false),
        "ha-dashboard" => render_ha(value),
        "acquire-coordinator-lease" => {
            if value.get("acquired").and_then(Value::as_bool) == Some(true) {
                println!("Lease acquired: {}", text(value, "lease_name"));
                println!("  Holder: {}", text(value, "holder_id"));
                println!("  TTL: {}s", raw(value, "ttl"));
                println!("  Expires at: {}", raw(value, "expires_at"));
            } else {
                println!("Lease acquisition failed — leadership held by another agent");
            }
        }
        "refresh-coordinator-lease" => {
            if value.get("refreshed").and_then(Value::as_bool) == Some(true) {
                println!("Lease refreshed: {}", text(value, "lease_name"));
                println!("  New expiry: {}", raw(value, "expires_at"));
            } else {
                eprintln!("Refresh failed: {}", text(value, "error"));
            }
        }
        "release-coordinator-lease" => {
            if value.get("released").and_then(Value::as_bool) == Some(true) {
                println!("Lease released: {}", text(value, "lease_name"));
            } else {
                println!("Release failed: {}", text(value, "error"));
            }
        }
        "claim-leadership" => {
            if value.get("claimed").and_then(Value::as_bool) == Some(true) {
                println!("Leadership claimed: {}", text(value, "lease_name"));
                println!("  Holder: {}", text(value, "holder_id"));
            } else {
                println!("Leadership claim failed");
            }
        }
        "spawn-subagent" => {
            println!("Spawn registered: {}", text(value, "spawn_id"));
            println!("  Parent: {}", text(value, "parent_agent_id"));
            println!("  Status: pending");
        }
        "list-pending-spawns" => render_spawns(value, id),
        "cancel-spawn" => {
            if value.get("cancelled").and_then(Value::as_bool) == Some(true) {
                println!("Spawn cancelled: {id}");
            } else if value.get("not_found").and_then(Value::as_bool) == Some(true) {
                eprintln!("Spawn not found or already consumed: {id}");
            } else {
                eprintln!("Cancel failed: {}", text(value, "error"));
            }
        }
        "request-subagent-deregistration" => {
            let child = pos.get(1).map(|s| s.as_str()).unwrap_or("");
            if value.get("requested").and_then(Value::as_bool) == Some(true) {
                println!("Stop requested for: {child}");
            } else if value.get("not_found").and_then(Value::as_bool) == Some(true) {
                eprintln!("Agent not found or not active: {child}");
            } else {
                eprintln!("Stop request failed: {}", text(value, "error"));
            }
        }
        "await-agent" => {
            println!("Agent: {id}");
            println!("  Status: {}", text(value, "status"));
            if value.get("awaited").and_then(Value::as_bool) == Some(true) {
                let waited = value.get("waited_s").and_then(Value::as_f64).unwrap_or(0.0);
                println!("  Waited: {waited:.1}s");
            } else {
                println!("  Timeout: {}s", raw(value, "timeout_s"));
            }
        }
        "await-subagent-stopped" => {
            if value.get("stopped").and_then(Value::as_bool) == Some(true) {
                println!("Agent stopped: {id}");
            } else {
                println!("Timeout waiting for: {id}");
                println!("  Escalate: call deregister_agent to force cleanup");
            }
        }
        "send-message" => println!(
            "MESSAGE SENT to {}",
            pos.get(1).map(|s| s.as_str()).unwrap_or("")
        ),
        "validate-spec" => {
            if value.get("valid").and_then(Value::as_bool) == Some(true) {
                println!("Coordination spec is valid");
            } else {
                println!("Coordination spec is INVALID");
                if let Some(items) = value.get("errors").and_then(Value::as_array) {
                    for item in items {
                        if let Some(text) = item.as_str() {
                            println!("  {text}");
                        }
                    }
                }
            }
        }
        "scan-project" => {
            println!(
                "Scanned {} file(s)",
                value
                    .get("scanned")
                    .or_else(|| value.get("files_scanned"))
                    .and_then(Value::as_i64)
                    .unwrap_or(0)
            );
            println!(
                "  Owned files: {}",
                value
                    .get("owned")
                    .or_else(|| value.get("owned_files"))
                    .and_then(Value::as_i64)
                    .unwrap_or(0)
            );
        }
        "dashboard" => render_dashboard(value),
        "agent-relations" => {
            let lineage = value.get("lineage");
            if let Some(lineage) = lineage {
                let ancestors = id_list(lineage, "ancestors");
                let descendants = id_list(lineage, "descendants");
                println!("Lineage for {id}:");
                println!(
                    "  Ancestors: {}",
                    if ancestors.is_empty() { "(none)" } else { &ancestors }
                );
                println!(
                    "  Descendants: {}",
                    if descendants.is_empty() {
                        "(none)"
                    } else {
                        &descendants
                    }
                );
            } else if let Some(items) = value.get("siblings").and_then(Value::as_array) {
                if items.is_empty() {
                    println!("No siblings for {id}");
                } else {
                    println!("{} sibling(s):", items.len());
                    for item in items {
                        println!("  {}: {}", text(item, "agent_id"), text(item, "status"));
                    }
                }
            } else {
                println!("{}", crate::json::dumps_pretty(value));
            }
        }
        "wait-for-locks" => {
            println!("Released: {}", list_or_none(value, "released"));
            println!("Timed out: {}", list_or_none(value, "timed_out"));
        }
        "wait-for-task" => {
            if value.get("timed_out").and_then(Value::as_bool) == Some(true) {
                println!("Timeout waiting for: {id}");
            } else if text(value, "status") == "failed" {
                println!("Task failed: {id}");
            } else {
                println!("Task completed: {id}");
            }
        }
        "wait-for-handoff" => {
            if value.get("timed_out").and_then(Value::as_bool) == Some(true) {
                println!("Timed out waiting for handoff {id}");
            } else {
                println!("Handoff {id} completed");
            }
        }
        "retry-task" => {
            if value.get("retried").and_then(Value::as_bool) == Some(true) {
                println!("Task resurrected: {id}");
            } else {
                println!("Retry failed: {}", text(value, "reason"));
            }
        }
        "dead-letter-queue" => {
            let items = value
                .get("dead_letter_tasks")
                .and_then(Value::as_array)
                .map(|rows| rows.to_vec())
                .unwrap_or_default();
            if items.is_empty() {
                println!("Dead letter queue is empty");
            } else {
                println!("Dead letter queue ({}):", items.len());
                for item in items {
                    println!("  {} — failed at {}", text(&item, "task_id"), text(&item, "failed_at"));
                }
            }
        }
        "acknowledge-broadcast" => {
            if value.get("acknowledged").and_then(Value::as_bool) == Some(true) {
                println!("Acknowledged broadcast {id} by {}", pos.get(1).map(|s| s.as_str()).unwrap_or(""));
            } else {
                println!("Failed: {}", text(value, "reason"));
            }
        }
        "get-conflicts" => {
            let items = value.get("conflicts").and_then(Value::as_array);
            match items {
                Some(items) if !items.is_empty() => {
                    println!("{} conflict(s):", items.len());
                    for item in items {
                        println!(
                            "  {} — {} vs {}",
                            text(item, "document_path"),
                            text(item, "agent_a"),
                            text(item, "agent_b")
                        );
                    }
                }
                _ => println!("No conflicts"),
            }
        }
        "load-spec" => {
            if value.get("loaded").and_then(Value::as_bool) == Some(true) {
                println!("Loaded coordination spec");
            } else {
                println!("Coordination spec not loaded");
                if let Some(err) = value.get("error").and_then(Value::as_str) {
                    println!("  {err}");
                }
            }
        }
        "agent-tree" => {
            if let Some(text) = value.get("text_tree").and_then(Value::as_str) {
                println!("{text}");
            }
        }
        "who" => println!("{}", crate::json::dumps_pretty(value)),
        _ => println!("{}", crate::json::dumps_pretty(value)),
    }
}

pub fn exit_code(command: &str, value: &Value) -> i32 {
    match command {
        "acquire-lock" => {
            if value.get("acquired").and_then(Value::as_bool) == Some(true) {
                0
            } else {
                4
            }
        }
        "refresh-coordinator-lease" => {
            if value.get("refreshed").and_then(Value::as_bool) == Some(true) {
                0
            } else {
                4
            }
        }
        "release-coordinator-lease" => {
            if value.get("released").and_then(Value::as_bool) == Some(true) {
                0
            } else {
                4
            }
        }
        "request-subagent-deregistration" => {
            if value.get("not_found").and_then(Value::as_bool) == Some(true) {
                3
            } else if value.get("requested").and_then(Value::as_bool) == Some(true) {
                0
            } else {
                1
            }
        }
        "cancel-spawn" => {
            if value.get("not_found").and_then(Value::as_bool) == Some(true) {
                3
            } else if value.get("cancelled").and_then(Value::as_bool) == Some(true) {
                0
            } else {
                1
            }
        }
        _ => 0,
    }
}

fn render_status(value: &Value) {
    println!("CoordinationHub Status");
    if let Value::Object(pairs) = value {
        for (key, item) in pairs {
            let label = key
                .split('_')
                .map(|word| {
                    let mut chars = word.chars();
                    match chars.next() {
                        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                        None => String::new(),
                    }
                })
                .collect::<Vec<_>>()
                .join(" ");
            println!("  {label}: {}", crate::json::dumps(item));
        }
    }
}

fn render_agents(value: &Value) {
    let Some(items) = value.get("agents").and_then(Value::as_array) else {
        println!("No agents registered");
        return;
    };
    if items.is_empty() {
        println!("No agents registered");
        return;
    }
    println!("{} agent(s):", items.len());
    for item in items {
        let id = text(item, "agent_id");
        let status = text(item, "status");
        let stale = item.get("stale").and_then(Value::as_bool).unwrap_or(false);
        let mark = if stale { "!" } else { " " };
        let tag = if stale { " (STALE)" } else { "" };
        println!(" {mark} {id}: {status}{tag}");
    }
}

fn render_lock(command: &str, value: &Value, path: &str) {
    if value.get("acquired").and_then(Value::as_bool) == Some(true) {
        println!("LOCKED: {path}");
        println!("  Agent: {}", text(value, "locked_by"));
        println!("  Expires: {}", raw(value, "expires_at"));
    } else if value.get("released").and_then(Value::as_bool) == Some(true) {
        println!("RELEASED: {path}");
    } else if value.get("refreshed").and_then(Value::as_bool) == Some(true) {
        println!("REFRESHED: {path}");
        println!("  Expires: {}", raw(value, "expires_at"));
    } else if command == "acquire-lock" {
        eprintln!(
            "FAILED: {path} is locked by {}",
            text(value, "locked_by")
        );
        eprintln!("  Expires: {}", raw(value, "expires_at"));
    } else {
        println!("FAILED: {}", text(value, "reason"));
    }
}

fn render_lock_list(value: &Value) {
    let Some(locks) = value.get("locks").and_then(Value::as_array) else {
        println!("No active locks");
        return;
    };
    if locks.is_empty() {
        println!("No active locks");
        return;
    }
    println!("Active locks ({}):", locks.len());
    for lock in locks {
        println!("  {}", text(lock, "document_path"));
        println!("    Held by: {}", text(lock, "locked_by"));
        println!("    Type: {}", text(lock, "lock_type"));
        let expires = lock.get("expires_at").and_then(Value::as_f64).unwrap_or(0.0);
        println!("    Expires: {expires:.0}");
    }
}

fn render_notes(value: &Value) {
    let Some(items) = value.get("notifications").and_then(Value::as_array) else {
        println!("No notifications");
        return;
    };
    if items.is_empty() {
        println!("No notifications");
        return;
    }
    println!("{} notification(s):", items.len());
    for item in items {
        println!(
            "  {}: {} by {}",
            text(item, "document_path"),
            text(item, "change_type"),
            text(item, "agent_id")
        );
    }
}

fn render_handoffs(value: &Value) {
    let Some(items) = value.get("handoffs").and_then(Value::as_array) else {
        println!("No handoffs found");
        return;
    };
    if items.is_empty() {
        println!("No handoffs found");
        return;
    }
    println!("{} handoff(s):", items.len());
    for item in items {
        println!(
            "  [{}] {} {} → {}",
            text(item, "status"),
            raw(item, "id"),
            text(item, "from_agent_id"),
            join_strings(item, "to_agents")
        );
    }
}

fn render_tasks(value: &Value) {
    let items = value
        .get("tasks")
        .or_else(|| value.get("subtasks"))
        .and_then(Value::as_array);
    let Some(items) = items else {
        println!("No tasks found");
        return;
    };
    if items.is_empty() {
        println!("No tasks found");
        return;
    }
    println!("Tasks ({}):", items.len());
    for item in items {
        let assigned = item
            .get("assigned_agent_id")
            .and_then(Value::as_str)
            .unwrap_or("unassigned");
        println!(
            "  [{}] {} — {assigned}",
            text(item, "status"),
            text(item, "id")
        );
    }
}

fn render_available(value: &Value) {
    let Some(items) = value.get("tasks").and_then(Value::as_array) else {
        println!("No available tasks");
        return;
    };
    if items.is_empty() {
        println!("No available tasks");
        return;
    }
    println!("Available tasks ({}):", items.len());
    for item in items {
        let desc = item.get("description").and_then(Value::as_str).unwrap_or("");
        println!("  {} — {desc}", text(item, "id"));
    }
}

fn render_intents(value: &Value) {
    let Some(items) = value.get("intents").and_then(Value::as_array) else {
        println!("No active work intents");
        return;
    };
    if items.is_empty() {
        println!("No active work intents");
        return;
    }
    println!("{} active intent(s):", items.len());
    for item in items {
        println!(
            "  {}: {} — {}",
            text(item, "agent_id"),
            text(item, "document_path"),
            text(item, "intent")
        );
    }
}

fn render_leader(value: &Value, full: bool) {
    let leader = value.get("leader");
    if leader.map(|item| matches!(item, Value::Null)).unwrap_or(true) {
        if full {
            println!("No active coordinator leader");
        } else {
            println!("No active leader");
        }
        return;
    }
    let leader = leader.unwrap();
    let holder = text(leader, "holder_id");
    if full {
        println!("Current leader: {holder}");
        println!("  Lease: {}", text(leader, "lease_name"));
        println!("  Expires: {}", raw(leader, "expires_at"));
    } else {
        println!("Leader: {holder} (expires {})", raw(leader, "expires_at"));
    }
}

fn render_ha(value: &Value) {
    let leader = value.get("leader");
    if leader.map(|item| matches!(item, Value::Null)).unwrap_or(true) {
        println!("No active coordinator lease");
        return;
    }
    let leader = leader.unwrap();
    println!("Coordinator lease:");
    println!("  Lease: {}", text(leader, "lease_name"));
    println!("  Holder: {}", text(leader, "holder_id"));
    println!("  Acquired at: {}", raw(leader, "acquired_at"));
    println!("  Expires at: {}", raw(leader, "expires_at"));
}

fn render_spawns(value: &Value, parent: &str) {
    let items = value
        .get("spawns")
        .and_then(Value::as_array)
        .map(|items| items.to_vec())
        .unwrap_or_default();
    if items.is_empty() {
        println!("No pending spawns.");
        return;
    }
    println!("Pending spawns for {parent}:");
    for item in items {
        println!("  {}", text(&item, "task_id"));
        println!(
            "    type={} | age=0s | desc={}",
            text(&item, "subagent_type"),
            text(&item, "description")
        );
    }
}

fn render_dashboard(value: &Value) {
    println!("{}", "=".repeat(60));
    println!("CoordinationHub Dashboard");
    println!("{}", "=".repeat(60));
    let agents = value.get("agents").and_then(Value::as_array);
    let locks = value.get("locks").and_then(Value::as_array);
    let tasks = value.get("tasks").and_then(Value::as_array);
    println!("\nAgents ({}):", agents.map(|i| i.len()).unwrap_or(0));
    if let Some(items) = agents {
        for item in items {
            let stale = if item.get("stale").and_then(Value::as_bool) == Some(true) {
                " (STALE)"
            } else {
                ""
            };
            println!("  {}: {}{stale}", text(item, "agent_id"), text(item, "status"));
        }
    }
    println!("\nLocks ({}):", locks.map(|i| i.len()).unwrap_or(0));
    if locks.map(|i| i.is_empty()).unwrap_or(true) {
        println!("  (none)");
    } else if let Some(items) = locks {
        for item in items {
            println!(
                "  {} — {} ({})",
                text(item, "document_path"),
                text(item, "locked_by"),
                text(item, "lock_type")
            );
        }
    }
    println!("\nTasks ({}):", tasks.map(|i| i.len()).unwrap_or(0));
    if tasks.map(|i| i.is_empty()).unwrap_or(true) {
        println!("  (none)");
    } else if let Some(items) = tasks {
        for item in items {
            println!("  [{}] {}", text(item, "status"), text(item, "id"));
        }
    }
    println!("{}", "=".repeat(60));
}

fn text(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn num(value: &Value, key: &str) -> i64 {
    value.get(key).and_then(Value::as_i64).unwrap_or(0)
}

fn raw(value: &Value, key: &str) -> String {
    value
        .get(key)
        .map(crate::json::dumps)
        .unwrap_or_else(|| "null".into())
}

fn id_list(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    item.as_str()
                        .or_else(|| item.get("agent_id").and_then(Value::as_str))
                })
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default()
}

fn list_or_none(value: &Value, key: &str) -> String {
    let items = value
        .get(key)
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default();
    if items.is_empty() {
        "(none)".into()
    } else {
        items
    }
}

fn join_strings(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default()
}

fn py_bool(value: bool) -> &'static str {
    if value {
        "True"
    } else {
        "False"
    }
}
