//! Document locks, conflicts, and change notifications.

use crate::db::{Conflict, Lock, Notification, State};
use crate::json::{self, Value};
use crate::util::{self, normalize_path};
use std::path::Path;

const DEFAULT_TTL: f64 = 300.0;

/// A heartbeat renews a lock once less than this share of its TTL is left.
pub const RENEW_FRACTION: f64 = 0.5;

/// Regions are editor line numbers: 1-based and inclusive, so 10-20 and
/// 20-30 share line 20. A lock without a region covers the whole file.
pub fn regions_overlap(a0: Option<i64>, a1: Option<i64>, b0: Option<i64>, b1: Option<i64>) -> bool {
    match (span(a0, a1), span(b0, b1)) {
        (Some((s1, e1)), Some((s2, e2))) => s1 <= e2 && s2 <= e1,
        _ => true,
    }
}

/// The lines a stored lock covers, or None for the whole file. New locks
/// always have a valid pair (see `check_region`); rows written by 1.2.0 or
/// earlier may not. Such a row is read so it can only block more, never
/// less: a start without an end runs to the end of the file, an inverted
/// pair is read low to high, and an end without a start is the whole file.
fn span(start: Option<i64>, end: Option<i64>) -> Option<(i64, i64)> {
    let start = start?;
    let end = end.unwrap_or(i64::MAX);
    Some((start.min(end), start.max(end)))
}

/// Checks a region given to acquire_lock, release_lock or refresh_lock:
/// both ends or neither, each 1 or more, start not after end.
pub fn check_region(start: Option<i64>, end: Option<i64>) -> Result<(), String> {
    const RULE: &str = "a region is an inclusive, 1-based line range: region_start <= region_end, both 1 or more";
    match (start, end) {
        (None, None) => Ok(()),
        (Some(_), None) => Err(format!(
            "region_end is missing: {RULE}. Pass region_end too (region_end = region_start locks one line), or omit both to lock the whole file"
        )),
        (None, Some(_)) => Err(format!(
            "region_start is missing: {RULE}. Pass region_start too, or omit both to lock the whole file"
        )),
        (Some(s), Some(_)) if s < 1 => Err(format!(
            "region_start must be 1 or more (got {s}): {RULE}. Line 1 is the first line of the file"
        )),
        (Some(_), Some(e)) if e < 1 => Err(format!(
            "region_end must be 1 or more (got {e}): {RULE}. Line 1 is the first line of the file"
        )),
        (Some(s), Some(e)) if s > e => Err(format!(
            "region_start ({s}) is after region_end ({e}): {RULE}. Pass the lower line as region_start"
        )),
        _ => Ok(()),
    }
}

/// The region check for the tools that take one, as an argument error.
pub fn check_region_args(tool: &str, args: &Value) -> Result<(), String> {
    if !matches!(tool, "acquire_lock" | "release_lock" | "refresh_lock") {
        return Ok(());
    }
    let read = |key: &str| match args.get(key) {
        None | Some(Value::Null) => None,
        Some(other) => other.as_i64(),
    };
    check_region(read("region_start"), read("region_end"))
        .map_err(|err| format!("Input validation error: {tool}: {err}"))
}

pub fn record_notification(
    state: &mut State,
    path: &str,
    change_type: &str,
    agent_id: &str,
    worktree: Option<String>,
) -> Value {
    let now = util::now();
    let id = state.next("notifications");
    state.notifications.push(Notification {
        id,
        document_path: path.to_string(),
        change_type: change_type.to_string(),
        agent_id: agent_id.to_string(),
        worktree_root: worktree,
        created_at: now,
    });
    json::obj(vec![
        ("recorded", json::b(true)),
        ("notification_id", json::i(id)),
        ("created_at", json::f(now)),
    ])
}

pub fn record_conflict(
    state: &mut State,
    path: &str,
    agent_a: &str,
    agent_b: &str,
    conflict_type: &str,
    resolution: &str,
    details: Option<String>,
) -> i64 {
    let id = state.next("conflicts");
    state.conflicts.push(Conflict {
        id,
        document_path: path.to_string(),
        agent_a: agent_a.to_string(),
        agent_b: agent_b.to_string(),
        conflict_type: conflict_type.to_string(),
        resolution: resolution.to_string(),
        details_json: details,
        created_at: util::now(),
    });
    id
}

fn lock_alive(lock: &Lock, now: f64) -> bool {
    lock.locked_at + lock.lock_ttl > now
}

// One parameter per `acquire_lock` argument plus the retry attempt number.
#[allow(clippy::too_many_arguments)]
pub fn acquire(
    state: &mut State,
    document_path: &str,
    agent_id: &str,
    lock_type: &str,
    ttl: f64,
    force: bool,
    region_start: Option<i64>,
    region_end: Option<i64>,
    project_root: Option<&Path>,
    attempt: i64,
) -> Value {
    let norm = normalize_path(document_path, project_root);
    let worktree = project_root.map(util::path_str);
    let now = util::now();
    if let Some(pos) = state.locks.iter().position(|lock| {
        lock.document_path == norm
            && lock.locked_by == agent_id
            && lock.region_start == region_start
            && lock.region_end == region_end
            && worktree_ok(&lock.worktree_root, worktree.as_deref())
    }) {
        let lock = &mut state.locks[pos];
        lock.locked_at = now;
        lock.lock_ttl = ttl;
        lock.lock_type = lock_type.to_string();
        return json::obj(vec![
            ("acquired", json::b(true)),
            ("document_path", json::s(&norm)),
            ("locked_by", json::s(agent_id)),
            ("expires_at", json::f(now + ttl)),
            ("region_start", json::opt_i(region_start)),
            ("region_end", json::opt_i(region_end)),
            ("attempts", json::i(attempt)),
        ]);
    }
    let conflicts = blocking_locks(
        state,
        &norm,
        agent_id,
        lock_type,
        (region_start, region_end),
        worktree.as_deref(),
        now,
    );
    if !conflicts.is_empty() && !force {
        for conflict in &conflicts {
            record_conflict(
                state,
                &norm,
                &conflict.locked_by,
                agent_id,
                "denied",
                "rejected",
                None,
            );
        }
        let first = &conflicts[0];
        let listed = conflicts
            .iter()
            .map(|c| {
                json::obj(vec![
                    ("locked_by", json::s(&c.locked_by)),
                    ("region_start", json::opt_i(c.region_start)),
                    ("region_end", json::opt_i(c.region_end)),
                ])
            })
            .collect();
        return json::obj(vec![
            ("acquired", json::b(false)),
            ("locked_by", json::s(&first.locked_by)),
            ("locked_at", json::f(first.locked_at)),
            ("expires_at", json::f(first.locked_at + first.lock_ttl)),
            ("worktree", json::opt_string(&first.worktree_root)),
            ("conflicts", json::arr(listed)),
            ("attempt", json::i(attempt)),
        ]);
    }
    if !conflicts.is_empty() && force {
        let ids: Vec<i64> = conflicts.iter().map(|c| c.id).collect();
        for conflict in &conflicts {
            record_conflict(
                state,
                &norm,
                &conflict.locked_by,
                agent_id,
                "lock_stolen",
                "force_overwritten",
                None,
            );
        }
        state.locks.retain(|lock| !ids.contains(&lock.id));
    }
    if let Some(scope_hit) = scope_violation(state, &norm, agent_id, project_root) {
        return json::obj(vec![
            ("acquired", json::b(false)),
            ("error", json::s("scope_violation")),
            ("scope_violation", scope_hit),
            ("attempts", json::i(attempt)),
        ]);
    }
    let id = state.next("locks");
    state.locks.push(Lock {
        id,
        document_path: norm.clone(),
        locked_by: agent_id.to_string(),
        locked_at: now,
        lock_ttl: ttl,
        lock_type: lock_type.to_string(),
        region_start,
        region_end,
        worktree_root: worktree.clone(),
    });
    record_notification(state, &norm, "locked", agent_id, worktree);
    let mut result = vec![
        ("acquired", json::b(true)),
        ("document_path", json::s(&norm)),
        ("locked_by", json::s(agent_id)),
        ("expires_at", json::f(now + ttl)),
        ("region_start", json::opt_i(region_start)),
        ("region_end", json::opt_i(region_end)),
        ("attempts", json::i(attempt)),
    ];
    if let Some(warning) = ownership_warning(state, &norm, agent_id, project_root) {
        result.push(("ownership_warning", warning));
    }
    if let Some(warning) = proximity_warning(state, &norm, agent_id) {
        result.push(("proximity_warning", warning));
    }
    state.record_event(
        "lock.acquired",
        json::obj(vec![
            ("document_path", json::s(&norm)),
            ("agent_id", json::s(agent_id)),
            ("lock_type", json::s(lock_type)),
            ("region_start", json::opt_i(region_start)),
            ("region_end", json::opt_i(region_end)),
        ]),
    );
    json::obj(result)
}

/// The live locks of other agents that keep `agent_id` from taking this
/// lock: same file and worktree, overlapping lines, not both shared.
fn blocking_locks(
    state: &State,
    norm: &str,
    agent_id: &str,
    lock_type: &str,
    (region_start, region_end): (Option<i64>, Option<i64>),
    worktree: Option<&str>,
    now: f64,
) -> Vec<Lock> {
    state
        .locks
        .iter()
        .filter(|lock| {
            lock.document_path == norm
                && lock_alive(lock, now)
                && lock.locked_by != agent_id
                && worktree_ok(&lock.worktree_root, worktree)
                && !(lock.lock_type == "shared" && lock_type == "shared")
                && regions_overlap(lock.region_start, lock.region_end, region_start, region_end)
        })
        .cloned()
        .collect()
}

/// True when `agent_id` already holds exactly this lock (a re-acquire
/// renews it whatever else is on the file).
fn holds_same(
    state: &State,
    norm: &str,
    agent_id: &str,
    (region_start, region_end): (Option<i64>, Option<i64>),
    worktree: Option<&str>,
) -> bool {
    state.locks.iter().any(|lock| {
        lock.document_path == norm
            && lock.locked_by == agent_id
            && lock.region_start == region_start
            && lock.region_end == region_end
            && worktree_ok(&lock.worktree_root, worktree)
    })
}

/// Most paths one `acquire_lock` or `release_lock` call takes.
pub const MAX_PATHS: usize = 500;

/// The paths of a call that passed `document_path` as a list, each as
/// given and normalised (relative paths resolve against the working
/// directory, as for one path, so the normalised form is not passed on),
/// duplicates dropped (first one kept), at least one and at most
/// `MAX_PATHS`.
pub fn path_list(
    tool: &str,
    items: &[Value],
    region: (Option<i64>, Option<i64>),
    project_root: Option<&Path>,
) -> Result<Vec<(String, String)>, String> {
    if items.is_empty() {
        return Err(format!(
            "{tool}: document_path is an empty list; pass one path, or a list of at least one"
        ));
    }
    if items.len() > MAX_PATHS {
        return Err(format!(
            "{tool}: document_path lists {} paths; at most {MAX_PATHS} fit in one call, so split the list",
            items.len()
        ));
    }
    let mut out: Vec<(String, String)> = Vec::with_capacity(items.len());
    for (index, item) in items.iter().enumerate() {
        let Some(text) = item.as_str() else {
            return Err(format!(
                "{tool}: document_path[{index}] must be a string path (got {})",
                item.py_type_name()
            ));
        };
        let norm = normalize_path(text, project_root);
        if !out.iter().any(|(_, seen)| *seen == norm) {
            out.push((text.to_string(), norm));
        }
    }
    if region.0.is_some() && out.len() > 1 {
        return Err(format!(
            "{tool}: region_start and region_end lock lines of one file, but document_path lists {} paths; lock a region with one path per call",
            out.len()
        ));
    }
    Ok(out)
}

/// Why one path of a list would not be locked.
enum Blocked {
    Held(Vec<Lock>),
    Scope(Value),
}

/// `acquire_lock` with a list of paths, in one store transaction. By
/// default all or nothing: every path is checked first, and if any is held
/// by another agent (or outside the agent's declared scope) none is taken
/// and every conflict is reported. `best_effort` takes the free paths and
/// reports the rest. The paths come from `path_list`.
#[allow(clippy::too_many_arguments)]
pub fn acquire_many(
    state: &mut State,
    paths: &[(String, String)],
    agent_id: &str,
    lock_type: &str,
    ttl: f64,
    force: bool,
    region: (Option<i64>, Option<i64>),
    best_effort: bool,
    project_root: Option<&Path>,
    attempt: i64,
) -> Value {
    let worktree = project_root.map(util::path_str);
    let now = util::now();
    let mut blocked: Vec<(String, Blocked)> = Vec::new();
    if !best_effort {
        for (_, norm) in paths {
            if holds_same(state, norm, agent_id, region, worktree.as_deref()) {
                continue;
            }
            let held = blocking_locks(state, norm, agent_id, lock_type, region, worktree.as_deref(), now);
            if !held.is_empty() && !force {
                blocked.push((norm.clone(), Blocked::Held(held)));
            } else if let Some(hit) = scope_violation(state, norm, agent_id, project_root) {
                blocked.push((norm.clone(), Blocked::Scope(hit)));
            }
        }
        for (norm, why) in &blocked {
            if let Blocked::Held(held) = why {
                for lock in held {
                    record_conflict(state, norm, &lock.locked_by, agent_id, "denied", "rejected", None);
                }
            }
        }
    }
    let mut acquired: Vec<String> = Vec::new();
    let mut warnings: Vec<Value> = Vec::new();
    if blocked.is_empty() {
        let before = state.locks.iter().map(|lock| lock.id).max().unwrap_or(0);
        for (given, norm) in paths {
            let reply = acquire(
                state, given, agent_id, lock_type, ttl, force, region.0, region.1, project_root, attempt,
            );
            if reply.get("acquired").and_then(Value::as_bool) == Some(true) {
                acquired.push(norm.clone());
                for key in ["ownership_warning", "proximity_warning"] {
                    if let Some(warning) = reply.get(key) {
                        warnings.push(json::obj(vec![
                            ("document_path", json::s(norm)),
                            (key, warning.clone()),
                        ]));
                    }
                }
                continue;
            }
            let why = match reply.get("scope_violation") {
                Some(hit) => Blocked::Scope(hit.clone()),
                None => Blocked::Held(blocking_locks(
                    state, norm, agent_id, lock_type, region, worktree.as_deref(), now,
                )),
            };
            blocked.push((norm.clone(), why));
        }
        if !best_effort && !blocked.is_empty() {
            // The checks above make this unreachable within one
            // transaction; if a path still failed, take back the locks this
            // call created so the call stays all or nothing.
            state.locks.retain(|lock| {
                !(lock.id > before && lock.locked_by == agent_id && acquired.contains(&lock.document_path))
            });
            acquired.clear();
            warnings.clear();
        }
    }
    let all = blocked.is_empty();
    let mut conflicts: Vec<Value> = Vec::new();
    let mut scope: Vec<Value> = Vec::new();
    for (norm, why) in &blocked {
        match why {
            Blocked::Held(held) => {
                for lock in held {
                    conflicts.push(json::obj(vec![
                        ("document_path", json::s(norm)),
                        ("locked_by", json::s(&lock.locked_by)),
                        ("lock_type", json::s(&lock.lock_type)),
                        ("region_start", json::opt_i(lock.region_start)),
                        ("region_end", json::opt_i(lock.region_end)),
                        ("locked_at", json::f(lock.locked_at)),
                        ("expires_at", json::f(lock.locked_at + lock.lock_ttl)),
                        ("worktree", json::opt_string(&lock.worktree_root)),
                    ]));
                }
            }
            Blocked::Scope(hit) => scope.push(hit.clone()),
        }
    }
    let holder = if all {
        json::s(agent_id)
    } else {
        conflicts
            .first()
            .and_then(|row| row.get("locked_by").cloned())
            .unwrap_or(Value::Null)
    };
    let failed: Vec<Value> = blocked.iter().map(|(norm, _)| json::s(norm)).collect();
    let mut reply = vec![
        ("acquired", json::b(all)),
        ("mode", json::s(if best_effort { "best_effort" } else { "all_or_nothing" })),
        ("locked_by", holder),
        ("acquired_paths", json::string_list(&acquired)),
        ("failed_paths", json::arr(failed)),
        ("conflicts", json::arr(conflicts)),
    ];
    if !acquired.is_empty() {
        reply.push(("expires_at", json::f(now + ttl)));
    }
    if region.0.is_some() {
        reply.push(("region_start", json::opt_i(region.0)));
        reply.push(("region_end", json::opt_i(region.1)));
    }
    if !scope.is_empty() {
        reply.push(("error", json::s("scope_violation")));
        reply.push(("scope_violations", json::arr(scope)));
    }
    if !warnings.is_empty() {
        reply.push(("warnings", json::arr(warnings)));
    }
    if !all && !best_effort {
        reply.push((
            "hint",
            json::s("nothing was locked (all or nothing). Wait (wait_for_locks), drop the held paths, or pass best_effort=true to take the free ones"),
        ));
    }
    reply.push(("attempts", json::i(attempt)));
    json::obj(reply)
}

/// `release_lock` with a list of paths (from `path_list`): each is
/// released as a single call would, and the reply lists what was released
/// and why each other path was not.
pub fn release_many(
    state: &mut State,
    paths: &[(String, String)],
    agent_id: &str,
    region: (Option<i64>, Option<i64>),
    project_root: Option<&Path>,
) -> Value {
    let mut released: Vec<String> = Vec::new();
    let mut failed: Vec<Value> = Vec::new();
    let mut count = 0_i64;
    for (given, norm) in paths {
        let reply = release(state, given, agent_id, region.0, region.1, project_root);
        if reply.get("released").and_then(Value::as_bool) == Some(true) {
            count += reply.get("count").and_then(Value::as_i64).unwrap_or(0);
            released.push(norm.clone());
        } else {
            let mut row = vec![("document_path".to_string(), json::s(norm))];
            if let Value::Object(pairs) = reply {
                row.extend(pairs.into_iter().filter(|(key, _)| key != "released"));
            }
            failed.push(Value::Object(row));
        }
    }
    json::obj(vec![
        ("released", json::b(failed.is_empty())),
        ("count", json::i(count)),
        ("released_paths", json::string_list(&released)),
        ("failed", json::arr(failed)),
    ])
}

fn worktree_ok(stored: &Option<String>, wanted: Option<&str>) -> bool {
    match wanted {
        None => true,
        Some(wanted) => stored.as_deref().map(|s| s == wanted).unwrap_or(true),
    }
}

fn scope_violation(
    state: &State,
    norm: &str,
    agent_id: &str,
    project_root: Option<&Path>,
) -> Option<Value> {
    let resp = state
        .responsibilities
        .iter()
        .find(|row| row.agent_id == agent_id)?;
    let raw = resp.scope.as_deref()?;
    if raw.is_empty() {
        return None;
    }
    let Ok(parsed) = json::parse_stored(raw) else {
        return None;
    };
    let items = parsed.as_array()?;
    if items.is_empty() {
        return None;
    }
    let prefixes: Vec<String> = items
        .iter()
        .filter_map(Value::as_str)
        .map(|s| s.to_string())
        .collect();
    if prefixes.is_empty() {
        return None;
    }
    for prefix in &prefixes {
        let norm_scope = normalize_path(prefix, project_root)
            .trim_end_matches('/')
            .to_string();
        if norm == norm_scope || norm.starts_with(&(norm_scope.clone() + "/")) {
            return None;
        }
    }
    let shown = json::arr(prefixes.iter().cloned().map(json::s).collect());
    Some(json::obj(vec![
        ("declared_scope", shown.clone()),
        ("attempted_path", json::s(norm)),
        (
            "message",
            json::s(format!(
                "Agent {agent_id} declared scope {} but attempted to lock {norm}",
                json::py_repr(&shown)
            )),
        ),
    ]))
}

fn ownership_warning(
    state: &mut State,
    norm: &str,
    agent_id: &str,
    project_root: Option<&Path>,
) -> Option<Value> {
    let owner = state
        .ownership
        .iter()
        .find(|row| row.document_path == norm)
        .map(|row| row.assigned_agent_id.clone())?;
    if owner == agent_id {
        return None;
    }
    let message = format!("Agent {agent_id} locked file owned by {owner}");
    let details = json::dumps(&json::obj(vec![("message", json::s(&message))]));
    record_conflict(
        state,
        norm,
        &owner,
        agent_id,
        "boundary_crossing",
        "allowed",
        Some(details),
    );
    let worktree = project_root.map(util::path_str);
    record_notification(state, norm, "boundary_crossing", agent_id, worktree);
    Some(json::obj(vec![
        ("owned_by", json::s(&owner)),
        (
            "message",
            json::s(format!("File is assigned to {owner} in file_ownership")),
        ),
    ]))
}

fn proximity_warning(state: &State, norm: &str, agent_id: &str) -> Option<Value> {
    let now = util::now();
    let conflicts: Vec<&crate::db::Intent> = state
        .intents
        .iter()
        .filter(|row| {
            row.document_path == norm && row.agent_id != agent_id && row.declared_at + row.ttl > now
        })
        .collect();
    if conflicts.is_empty() {
        return None;
    }
    let ids: Vec<String> = conflicts.iter().map(|row| row.agent_id.clone()).collect();
    let listed = conflicts
        .iter()
        .map(|row| {
            json::obj(vec![
                ("agent_id", json::s(&row.agent_id)),
                ("intent", json::s(&row.intent)),
            ])
        })
        .collect();
    let id_text = json::py_repr(&json::string_list(&ids));
    Some(json::obj(vec![
        ("conflicting_agents", json::arr(listed)),
        (
            "message",
            json::s(format!(
                "Agents {id_text} have declared intent to work on {norm}"
            )),
        ),
    ]))
}

pub fn release(
    state: &mut State,
    document_path: &str,
    agent_id: &str,
    region_start: Option<i64>,
    region_end: Option<i64>,
    project_root: Option<&Path>,
) -> Value {
    let norm = normalize_path(document_path, project_root);
    let now = util::now();
    let same_region = |lock: &Lock| {
        if region_start.is_some() {
            lock.region_start == region_start && lock.region_end == region_end
        } else {
            lock.region_start.is_none()
        }
    };
    let rows: Vec<&Lock> = state
        .locks
        .iter()
        .filter(|lock| lock.document_path == norm && same_region(lock))
        .collect();
    let any_rows = !rows.is_empty();
    let own: Vec<(i64, bool)> = rows
        .iter()
        .filter(|lock| lock.locked_by == agent_id)
        .map(|lock| (lock.id, lock_alive(lock, now)))
        .collect();
    if !own.is_empty() && own.iter().all(|(_, alive)| !alive) {
        // Every row this agent has here ran out of TTL: say so instead of
        // releasing a lock the agent no longer had.
        let (_, expired) = take_locks(state, now, |lock| {
            lock.document_path == norm && lock.locked_by == agent_id && same_region(lock)
        });
        let at = expired.iter().map(expiry).fold(f64::MIN, f64::max);
        clear_lapse(state, agent_id, Some((&norm, region_start, region_end)));
        let holder = live_holder(state, &norm, agent_id, now);
        return expired_reply("released", &norm, at, holder.as_deref(), now);
    }
    if own.is_empty() {
        if !any_rows && region_start.is_none() {
            let regions: Vec<Value> = state
                .locks
                .iter()
                .filter(|lock| {
                    lock.document_path == norm
                        && lock.locked_by == agent_id
                        && lock.region_start.is_some()
                })
                .map(|lock| {
                    json::obj(vec![
                        ("region_start", json::opt_i(lock.region_start)),
                        ("region_end", json::opt_i(lock.region_end)),
                    ])
                })
                .collect();
            if !regions.is_empty() {
                return json::obj(vec![
                    ("released", json::b(false)),
                    ("reason", json::s("region_required")),
                    ("region_locks", json::arr(regions)),
                ]);
            }
        }
        if let Some(lapse) = lapses(state, agent_id)
            .into_iter()
            .find(|lapse| lapse.matches(&norm, region_start, region_end))
        {
            clear_lapse(state, agent_id, Some((&norm, region_start, region_end)));
            let holder = live_holder(state, &norm, agent_id, now);
            return expired_reply("released", &norm, lapse.expired_at, holder.as_deref(), now);
        }
        return json::obj(vec![
            ("released", json::b(false)),
            ("reason", json::s(if any_rows { "not_owner" } else { "not_locked" })),
        ]);
    }
    let owned: Vec<i64> = own.iter().map(|(id, _)| *id).collect();
    let count = owned.len() as i64;
    state.locks.retain(|lock| !owned.contains(&lock.id));
    let worktree = project_root.map(util::path_str);
    record_notification(state, &norm, "unlocked", agent_id, worktree);
    state.record_event(
        "lock.released",
        json::obj(vec![
            ("document_path", json::s(&norm)),
            ("agent_id", json::s(agent_id)),
            ("region_start", json::opt_i(region_start)),
            ("region_end", json::opt_i(region_end)),
        ]),
    );
    json::obj(vec![("released", json::b(true)), ("count", json::i(count))])
}

pub fn refresh(
    state: &mut State,
    document_path: &str,
    agent_id: &str,
    ttl: f64,
    region_start: Option<i64>,
    region_end: Option<i64>,
    project_root: Option<&Path>,
) -> Value {
    let norm = normalize_path(document_path, project_root);
    let now = util::now();
    let mine = |lock: &Lock| {
        lock.document_path == norm
            && lock.locked_by == agent_id
            && lock.region_start == region_start
            && lock.region_end == region_end
    };
    if let Some(lock) = state.locks.iter_mut().find(|lock| mine(lock)) {
        if lock_alive(lock, now) {
            lock.locked_at = now;
            lock.lock_ttl = ttl;
            return json::obj(vec![
                ("refreshed", json::b(true)),
                ("expires_at", json::f(now + ttl)),
            ]);
        }
        let (_, expired) = take_locks(state, now, mine);
        let at = expired.iter().map(expiry).fold(f64::MIN, f64::max);
        clear_lapse(state, agent_id, Some((&norm, region_start, region_end)));
        let holder = live_holder(state, &norm, agent_id, now);
        return expired_reply("refreshed", &norm, at, holder.as_deref(), now);
    }
    if let Some(lapse) = lapses(state, agent_id)
        .into_iter()
        .find(|lapse| lapse.matches(&norm, region_start, region_end))
    {
        clear_lapse(state, agent_id, Some((&norm, region_start, region_end)));
        let holder = live_holder(state, &norm, agent_id, now);
        return expired_reply("refreshed", &norm, lapse.expired_at, holder.as_deref(), now);
    }
    json::obj(vec![
        ("refreshed", json::b(false)),
        ("reason", json::s("not_locked")),
    ])
}

/// The reply to releasing or refreshing a lock whose TTL ran out: when it
/// lapsed, who holds the file now, and what to do next.
fn expired_reply(verb: &str, path: &str, expired_at: f64, holder: Option<&str>, now: f64) -> Value {
    let ago = (now - expired_at).max(0.0).round() as i64;
    let next = match holder {
        Some(other) => format!("{other} holds it now; ask them or wait_for_locks, then acquire_lock"),
        None => "call acquire_lock again if you still need it".to_string(),
    };
    let mut pairs = vec![
        (verb, json::b(false)),
        ("reason", json::s("expired")),
        ("expired_at", json::f(expired_at)),
    ];
    if let Some(other) = holder {
        pairs.push(("locked_by", json::s(other)));
    }
    pairs.push((
        "error",
        json::s(format!(
            "your lock on {path} expired {ago} s ago (its ttl ran out; heartbeat renews held locks); {next}"
        )),
    ));
    json::obj(pairs)
}

fn live_holder(state: &State, path: &str, agent_id: &str, now: f64) -> Option<String> {
    state
        .locks
        .iter()
        .find(|lock| lock.document_path == path && lock.locked_by != agent_id && lock_alive(lock, now))
        .map(|lock| lock.locked_by.clone())
}

fn expiry(lock: &Lock) -> f64 {
    lock.locked_at + lock.lock_ttl
}

/// Removes the locks `pick` selects and returns them as (live, expired).
/// Each expired one is recorded as a `lock.expired` event, so its holder
/// can later be told the lock lapsed rather than that it never existed.
pub fn take_locks(
    state: &mut State,
    now: f64,
    pick: impl Fn(&Lock) -> bool,
) -> (Vec<Lock>, Vec<Lock>) {
    let mut live = Vec::new();
    let mut expired = Vec::new();
    let mut kept = Vec::with_capacity(state.locks.len());
    for lock in std::mem::take(&mut state.locks) {
        if !pick(&lock) {
            kept.push(lock);
        } else if lock_alive(&lock, now) {
            live.push(lock);
        } else {
            expired.push(lock);
        }
    }
    state.locks = kept;
    for lock in &expired {
        state.record_event(
            "lock.expired",
            json::obj(vec![
                ("document_path", json::s(&lock.document_path)),
                ("agent_id", json::s(&lock.locked_by)),
                ("region_start", json::opt_i(lock.region_start)),
                ("region_end", json::opt_i(lock.region_end)),
                ("expired_at", json::f(expiry(lock))),
            ]),
        );
    }
    (live, expired)
}

/// A lock that ran out of TTL and whose holder has not yet been told so by
/// a release, refresh, re-acquire or deregistration.
#[derive(Clone, Debug, PartialEq)]
pub struct Lapse {
    pub document_path: String,
    pub region_start: Option<i64>,
    pub region_end: Option<i64>,
    pub expired_at: f64,
}

impl Lapse {
    fn matches(&self, path: &str, start: Option<i64>, end: Option<i64>) -> bool {
        self.document_path == path && self.region_start == start && self.region_end == end
    }

    pub fn to_value(&self) -> Value {
        let mut pairs = vec![
            ("document_path", json::s(&self.document_path)),
            ("expired_at", json::f(self.expired_at)),
        ];
        if self.region_start.is_some() {
            pairs.push(("region_start", json::opt_i(self.region_start)));
            pairs.push(("region_end", json::opt_i(self.region_end)));
        }
        json::obj(pairs)
    }
}

/// The agent's lapsed locks, read from the event journal. A `lock.expired`
/// event stays open until the same agent acquires, releases or clears that
/// lock, releases all its locks, or deregisters. Keeping them as events
/// means no new journal table, which a 1.1 binary sharing the store could
/// not read.
pub fn lapses(state: &State, agent_id: &str) -> Vec<Lapse> {
    let needle = json::dumps(&json::s(agent_id));
    let mut open: Vec<Lapse> = Vec::new();
    for event in &state.hub_events {
        let topic = event.topic.as_str();
        if !matches!(
            topic,
            "lock.expired"
                | "lock.acquired"
                | "lock.released"
                | "lock.expired_cleared"
                | "agent.deregistered"
        ) || !event.payload.contains(&needle)
        {
            continue;
        }
        let Ok(payload) = json::parse_stored(&event.payload) else {
            continue;
        };
        if payload.get("agent_id").and_then(Value::as_str) != Some(agent_id) {
            continue;
        }
        let bulk = payload.get("bulk").and_then(Value::as_bool) == Some(true);
        if topic == "agent.deregistered" || bulk {
            open.clear();
            continue;
        }
        let path = payload.get("document_path").and_then(Value::as_str).unwrap_or("");
        let start = payload.get("region_start").and_then(Value::as_i64);
        let end = payload.get("region_end").and_then(Value::as_i64);
        open.retain(|lapse| !lapse.matches(path, start, end));
        if topic == "lock.expired" {
            open.push(Lapse {
                document_path: path.to_string(),
                region_start: start,
                region_end: end,
                expired_at: payload
                    .get("expired_at")
                    .and_then(Value::as_f64)
                    .unwrap_or(event.created_at),
            });
        }
    }
    open
}

/// Marks lapses as reported: one lock (path and region), or all of the
/// agent's when `which` is None.
pub fn clear_lapse(
    state: &mut State,
    agent_id: &str,
    which: Option<(&str, Option<i64>, Option<i64>)>,
) {
    let payload = match which {
        Some((path, start, end)) => json::obj(vec![
            ("agent_id", json::s(agent_id)),
            ("document_path", json::s(path)),
            ("region_start", json::opt_i(start)),
            ("region_end", json::opt_i(end)),
        ]),
        None => json::obj(vec![("agent_id", json::s(agent_id)), ("bulk", json::b(true))]),
    };
    state.record_event("lock.expired_cleared", payload);
}

/// Heartbeat renewal: every live lock of `agent_id` with less than half of
/// its TTL left starts a fresh TTL (its `locked_at` becomes now). Returns
/// how many were renewed and the locks that still run out before the next
/// heartbeat is due (a TTL shorter than `next_beat`).
pub fn renew(state: &mut State, agent_id: &str, now: f64, next_beat: f64) -> (i64, Vec<Value>) {
    let mut renewed = 0;
    let mut soon = Vec::new();
    for lock in state.locks.iter_mut().filter(|lock| lock.locked_by == agent_id) {
        if !lock_alive(lock, now) {
            continue;
        }
        if expiry(lock) - now < lock.lock_ttl * RENEW_FRACTION {
            lock.locked_at = now;
            renewed += 1;
        }
        let left = expiry(lock) - now;
        if left < next_beat {
            soon.push(expiring(lock, left));
        }
    }
    (renewed, soon)
}

/// For `get_notifications`: the live locks of `agent_id` a heartbeat would
/// renew now (less than half their TTL left), and its lapsed locks.
pub fn warnings(state: &State, agent_id: &str, now: f64) -> (Vec<Value>, Vec<Value>) {
    let mut soon = Vec::new();
    let mut gone = lapses(state, agent_id);
    for lock in state.locks.iter().filter(|lock| lock.locked_by == agent_id) {
        let left = expiry(lock) - now;
        if left <= 0.0 {
            gone.retain(|lapse| {
                !lapse.matches(&lock.document_path, lock.region_start, lock.region_end)
            });
            gone.push(Lapse {
                document_path: lock.document_path.clone(),
                region_start: lock.region_start,
                region_end: lock.region_end,
                expired_at: expiry(lock),
            });
        } else if left < lock.lock_ttl * RENEW_FRACTION {
            soon.push(expiring(lock, left));
        }
    }
    (soon, gone.iter().map(Lapse::to_value).collect())
}

fn expiring(lock: &Lock, left: f64) -> Value {
    let mut pairs = vec![
        ("document_path", json::s(&lock.document_path)),
        ("expires_in_s", json::f((left * 10.0).round() / 10.0)),
    ];
    if lock.region_start.is_some() {
        pairs.push(("region_start", json::opt_i(lock.region_start)));
        pairs.push(("region_end", json::opt_i(lock.region_end)));
    }
    json::obj(pairs)
}

pub fn status(state: &State, document_path: &str, project_root: Option<&Path>) -> Value {
    let norm = normalize_path(document_path, project_root);
    let now = util::now();
    let active: Vec<&Lock> = state
        .locks
        .iter()
        .filter(|lock| lock.document_path == norm && lock_alive(lock, now))
        .collect();
    if active.is_empty() {
        return json::obj(vec![("locked", json::b(false))]);
    }
    let one = |lock: &Lock| {
        json::obj(vec![
            ("locked_by", json::s(&lock.locked_by)),
            ("locked_at", json::f(lock.locked_at)),
            ("expires_at", json::f(lock.locked_at + lock.lock_ttl)),
            ("lock_type", json::s(&lock.lock_type)),
            ("region_start", json::opt_i(lock.region_start)),
            ("region_end", json::opt_i(lock.region_end)),
            ("worktree", json::opt_string(&lock.worktree_root)),
        ])
    };
    if active.len() == 1 {
        let lock = active[0];
        return Value::Object(vec![
            ("locked".into(), json::b(true)),
            ("locked_by".into(), json::s(&lock.locked_by)),
            ("locked_at".into(), json::f(lock.locked_at)),
            ("expires_at".into(), json::f(lock.locked_at + lock.lock_ttl)),
            ("lock_type".into(), json::s(&lock.lock_type)),
            ("region_start".into(), json::opt_i(lock.region_start)),
            ("region_end".into(), json::opt_i(lock.region_end)),
            ("worktree".into(), json::opt_string(&lock.worktree_root)),
        ]);
    }
    json::obj(vec![
        ("locked", json::b(true)),
        ("holders", json::arr(active.into_iter().map(one).collect())),
    ])
}

pub fn list_locks(state: &State, agent_id: Option<&str>) -> Value {
    let now = util::now();
    let mut seen_paths: Vec<String> = Vec::new();
    for lock in &state.locks {
        if lock_alive(lock, now) && !seen_paths.iter().any(|p| p == &lock.document_path) {
            seen_paths.push(lock.document_path.clone());
        }
    }
    let mut locks = Vec::new();
    for path in seen_paths {
        for lock in state
            .locks
            .iter()
            .filter(|lock| lock.document_path == path && lock_alive(lock, now))
        {
            if agent_id.map(|id| lock.locked_by == id).unwrap_or(true) {
                locks.push(json::obj(vec![
                    ("document_path", json::s(&lock.document_path)),
                    ("locked_by", json::s(&lock.locked_by)),
                    ("locked_at", json::f(lock.locked_at)),
                    ("expires_at", json::f(lock.locked_at + lock.lock_ttl)),
                    ("lock_type", json::s(&lock.lock_type)),
                    ("region_start", json::opt_i(lock.region_start)),
                    ("region_end", json::opt_i(lock.region_end)),
                    ("worktree", json::opt_string(&lock.worktree_root)),
                ]));
            }
        }
    }
    let count = locks.len() as i64;
    json::obj(vec![("locks", json::arr(locks)), ("count", json::i(count))])
}

/// Drops every lock of `agent_id`. `released` counts the live ones; locks
/// whose TTL had already run out, and lapses not yet reported, are listed
/// under `expired` (left out when there are none) and marked reported.
pub fn release_agent(state: &mut State, agent_id: &str) -> Value {
    let (live, _) = take_locks(state, util::now(), |lock| lock.locked_by == agent_id);
    let gone = lapses(state, agent_id);
    let mut pairs = vec![("released", json::i(live.len() as i64))];
    if !gone.is_empty() {
        clear_lapse(state, agent_id, None);
        pairs.push(("expired", json::arr(gone.iter().map(Lapse::to_value).collect())));
    }
    json::obj(pairs)
}

pub fn reap_expired(state: &mut State, grace: f64) -> Value {
    let now = util::now();
    if grace > 0.0 {
        let fresh: Vec<String> = state
            .agents
            .iter()
            .filter(|agent| agent.status == "active" && agent.last_heartbeat > now - grace)
            .map(|agent| agent.agent_id.clone())
            .collect();
        for lock in &mut state.locks {
            if lock.locked_at + lock.lock_ttl < now && fresh.iter().any(|id| id == &lock.locked_by)
            {
                lock.locked_at = now;
                if lock.lock_ttl < grace {
                    lock.lock_ttl = grace;
                }
            }
        }
    }
    // A lock at exactly its expiry instant is kept, as before.
    let (_, expired) = take_locks(state, now, |lock| lock.locked_at + lock.lock_ttl < now);
    json::obj(vec![("reaped", json::i(expired.len() as i64))])
}

pub fn notify(
    state: &mut State,
    document_path: &str,
    change_type: &str,
    agent_id: &str,
    project_root: Option<&Path>,
) -> Value {
    let norm = normalize_path(document_path, project_root);
    let worktree = project_root.map(util::path_str);
    let result = record_notification(state, &norm, change_type, agent_id, worktree);
    let created_at = result.get("created_at").and_then(Value::as_f64).unwrap_or(util::now());
    let notification_id = result.get("notification_id").and_then(Value::as_i64).unwrap_or(0);
    state.record_event(
        "notification.created",
        json::obj(vec![
            ("document_path", json::s(&norm)),
            ("change_type", json::s(change_type)),
            ("agent_id", json::s(agent_id)),
            ("created_at", json::f(created_at)),
            ("notification_id", json::i(notification_id)),
        ]),
    );
    result
}

pub fn prune_notifications(state: &mut State, max_age: Option<f64>, max_entries: Option<i64>) -> i64 {
    let now = util::now();
    let before = state.notifications.len() + state.hub_events.len();
    if let Some(age) = max_age {
        let cutoff = now - age;
        state.notifications.retain(|row| row.created_at >= cutoff);
        state.hub_events.retain(|row| row.created_at >= cutoff);
    }
    if let Some(max_entries) = max_entries {
        trim_oldest(&mut state.notifications, max_entries, |row| (row.created_at, row.id));
        trim_oldest(&mut state.hub_events, max_entries, |row| (row.created_at, row.id));
    }
    (before - state.notifications.len() - state.hub_events.len()) as i64
}

fn trim_oldest<T>(rows: &mut Vec<T>, max_entries: i64, key: impl Fn(&T) -> (f64, i64)) {
    // `DELETE ... ORDER BY created_at LIMIT count - max_entries`: a negative
    // max_entries leaves an excess above the row count, so every row goes.
    if rows.len() as i64 <= max_entries {
        return;
    }
    let mut order: Vec<usize> = (0..rows.len()).collect();
    order.sort_by(|&a, &b| {
        let (at, aid) = key(&rows[a]);
        let (bt, bid) = key(&rows[b]);
        at.partial_cmp(&bt)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(aid.cmp(&bid))
    });
    let excess = (rows.len() as i64 - max_entries).min(rows.len() as i64) as usize;
    let drop: Vec<usize> = order.into_iter().take(excess).collect();
    let mut keep = vec![true; rows.len()];
    for index in drop {
        keep[index] = false;
    }
    let mut index = 0;
    rows.retain(|_| {
        let ok = keep[index];
        index += 1;
        ok
    });
}

pub fn notifications(
    state: &State,
    since: Option<f64>,
    exclude: Option<&str>,
    limit: i64,
) -> Value {
    let mut rows: Vec<&Notification> = state
        .notifications
        .iter()
        .filter(|row| since.map(|cut| row.created_at > cut).unwrap_or(true))
        .filter(|row| exclude.map(|id| row.agent_id != id).unwrap_or(true))
        .collect();
    rows.sort_by(|a, b| b.id.cmp(&a.id));
    if limit >= 0 {
        rows.truncate(limit as usize);
    }
    let items = rows
        .into_iter()
        .map(|row| {
            json::obj(vec![
                ("id", json::i(row.id)),
                ("document_path", json::s(&row.document_path)),
                ("change_type", json::s(&row.change_type)),
                ("agent_id", json::s(&row.agent_id)),
                ("worktree_root", json::opt_string(&row.worktree_root)),
                ("created_at", json::f(row.created_at)),
            ])
        })
        .collect::<Vec<_>>();
    json::obj(vec![("notifications", json::arr(items))])
}

pub fn conflicts(
    state: &State,
    document_path: Option<&str>,
    agent_id: Option<&str>,
    limit: i64,
    project_root: Option<&Path>,
) -> Value {
    let norm = document_path.map(|path| normalize_path(path, project_root));
    let mut rows: Vec<&Conflict> = state
        .conflicts
        .iter()
        .filter(|row| {
            norm.as_ref()
                .map(|path| row.document_path == *path)
                .unwrap_or(true)
        })
        .filter(|row| {
            agent_id
                .map(|id| row.agent_a == id || row.agent_b == id)
                .unwrap_or(true)
        })
        .collect();
    rows.sort_by(|a, b| {
        b.created_at
            .partial_cmp(&a.created_at)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    rows.truncate(crate::util::sql_limit(limit));
    let items = rows
        .into_iter()
        .map(|row| {
            json::obj(vec![
                ("id", json::i(row.id)),
                ("document_path", json::s(&row.document_path)),
                ("agent_a", json::s(&row.agent_a)),
                ("agent_b", json::s(&row.agent_b)),
                ("conflict_type", json::s(&row.conflict_type)),
                ("resolution", json::s(&row.resolution)),
                ("details_json", json::opt_string(&row.details_json)),
                ("created_at", json::f(row.created_at)),
            ])
        })
        .collect::<Vec<_>>();
    json::obj(vec![("conflicts", json::arr(items))])
}

pub fn hotspots(state: &State, limit: i64) -> Value {
    let mut paths: Vec<String> = Vec::new();
    for row in &state.conflicts {
        if !paths.iter().any(|path| path == &row.document_path) {
            paths.push(row.document_path.clone());
        }
    }
    let mut ranked: Vec<(String, i64, Vec<String>, i64)> = paths
        .into_iter()
        .map(|path| {
            let rows: Vec<&Conflict> = state
                .conflicts
                .iter()
                .filter(|row| row.document_path == path)
                .collect();
            let mut agents: Vec<String> = Vec::new();
            for row in &rows {
                if !agents.iter().any(|id| id == &row.agent_a) {
                    agents.push(row.agent_a.clone());
                }
                if !agents.iter().any(|id| id == &row.agent_b) {
                    agents.push(row.agent_b.clone());
                }
            }
            agents.sort();
            let first_id = rows.iter().map(|row| row.id).min().unwrap_or(0);
            let count = rows.len() as i64;
            (path, count, agents, first_id)
        })
        .collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    ranked.truncate(crate::util::sql_limit(limit));
    let items = ranked
        .into_iter()
        .map(|(path, count, agents, _)| {
            json::obj(vec![
                ("document_path", json::s(path)),
                ("conflict_count", json::i(count)),
                ("agents_involved", json::string_list(&agents)),
            ])
        })
        .collect::<Vec<_>>();
    let total = items.len() as i64;
    json::obj(vec![
        ("hotspots", json::arr(items)),
        ("total", json::i(total)),
    ])
}

pub fn default_ttl() -> f64 {
    DEFAULT_TTL
}
