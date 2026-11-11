//! Argparse parity: exact stdout, stderr and exit code captured from
//! unmodified coordinationhub 0.8.0 on Python 3.12 (COLUMNS unset unless
//! named, stdout not a terminal). Each case runs in a fresh store after
//! `register viola` and `register lute --parent-id viola`. The 1.1.0
//! additions (`-V/--version`, `assign-task --force`) were captured from the
//! same parser with them declared by argparse's own `version` and
//! `store_true` actions (scratch wrapper around 0.8.0 `create_parser`).

mod common;

struct Case {
    args: &'static str,
    columns: Option<&'static str>,
    rc: i32,
    stdout: &'static str,
    stderr: &'static str,
}

const CASES: &[Case] = &[
    Case {
        args: r#"query-tasks by_agent --agent-id viola/x"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub [-h] [-V]
                       {serve,serve-mcp,serve-sse,status,register,heartbeat,deregister,list-agents,agent-relations,acquire-lock,release-lock,refresh-lock,lock-status,list-locks,admin-locks,broadcast,acknowledge-broadcast,wait-for-broadcast-acks,acknowledge-handoff,complete-handoff,cancel-handoff,get-handoffs,wait-for-handoff,wait-for-locks,notify-change,get-notifications,prune-notifications,wait-for-notifications,get-conflicts,contention-hotspots,load-spec,validate-spec,scan-project,dashboard,agent-status,agent-tree,assess,doctor,init,auto-start-dashboard,watch,await-agent,send-message,get-messages,mark-messages-read,create-task,assign-task,update-task-status,query-tasks,create-subtask,retry-task,dead-letter-queue,task-failure-history,wait-for-task,get-available-tasks,declare-work-intent,get-work-intents,clear-work-intent,declare-dependency,manage-dependencies,satisfy-dependency,get-all-dependencies,acquire-coordinator-lease,refresh-coordinator-lease,release-coordinator-lease,get-leader,claim-leadership,leader-status,ha-dashboard,spawn-subagent,report-subagent-spawned,list-pending-spawns,cancel-spawn,request-subagent-deregistration,await-subagent-stopped}
                       ...
coordinationhub: error: unrecognized arguments: --agent-id viola/x
"#,
    },
    Case {
        args: r#"acquire-lock m/q.ts lute --lock-type sharded"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub acquire-lock [-h] [--storage-dir STORAGE_DIR]
                                    [--project-root PROJECT_ROOT]
                                    [--namespace NAMESPACE] [-j] [--replica]
                                    [--lock-type {exclusive,shared}]
                                    [--ttl TTL] [--force]
                                    [--region-start REGION_START]
                                    [--region-end REGION_END] [--retry]
                                    [--max-retries MAX_RETRIES]
                                    [--backoff-ms BACKOFF_MS]
                                    [--timeout-ms TIMEOUT_MS]
                                    document_path agent_id
coordinationhub acquire-lock: error: argument --lock-type: invalid choice: 'sharded' (choose from exclusive, shared)
"#,
    },
    Case {
        args: r#"query-tasks bogus_kind"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub query-tasks [-h] [--storage-dir STORAGE_DIR]
                                   [--project-root PROJECT_ROOT]
                                   [--namespace NAMESPACE] [-j] [--replica]
                                   [--task-id TASK_ID]
                                   [--parent-agent-id PARENT_AGENT_ID]
                                   [--assigned-agent-id ASSIGNED_AGENT_ID]
                                   [--parent-task-id PARENT_TASK_ID]
                                   [--root-task-id ROOT_TASK_ID]
                                   {task,child,by_agent,all,subtasks,tree}
coordinationhub query-tasks: error: argument query_type: invalid choice: 'bogus_kind' (choose from task, child, by_agent, all, subtasks, tree)
"#,
    },
    Case {
        args: r#"get-notifications --limit abc"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub get-notifications [-h] [--storage-dir STORAGE_DIR]
                                         [--project-root PROJECT_ROOT]
                                         [--namespace NAMESPACE] [-j]
                                         [--replica] [--since SINCE]
                                         [--exclude-agent EXCLUDE_AGENT]
                                         [--limit LIMIT]
coordinationhub get-notifications: error: argument --limit: invalid int value: 'abc'
"#,
    },
    Case {
        args: r#"acquire-lock m/q2.ts lute --ttl ten"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub acquire-lock [-h] [--storage-dir STORAGE_DIR]
                                    [--project-root PROJECT_ROOT]
                                    [--namespace NAMESPACE] [-j] [--replica]
                                    [--lock-type {exclusive,shared}]
                                    [--ttl TTL] [--force]
                                    [--region-start REGION_START]
                                    [--region-end REGION_END] [--retry]
                                    [--max-retries MAX_RETRIES]
                                    [--backoff-ms BACKOFF_MS]
                                    [--timeout-ms TIMEOUT_MS]
                                    document_path agent_id
coordinationhub acquire-lock: error: argument --ttl: invalid float value: 'ten'
"#,
    },
    Case {
        args: r#"broadcast viola --message -loud"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub broadcast [-h] [--storage-dir STORAGE_DIR]
                                 [--project-root PROJECT_ROOT]
                                 [--namespace NAMESPACE] [-j] [--replica]
                                 [--document-path DOCUMENT_PATH]
                                 [--handoff-targets HANDOFF_TARGETS [HANDOFF_TARGETS ...]]
                                 [--require-ack] [--message MESSAGE]
                                 agent_id
coordinationhub broadcast: error: argument --message: expected one argument
"#,
    },
    Case {
        args: r#"register -dash-id"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub register [-h] [--storage-dir STORAGE_DIR]
                                [--project-root PROJECT_ROOT]
                                [--namespace NAMESPACE] [-j] [--replica]
                                [--parent-id PARENT_ID]
                                [--graph-agent-id GRAPH_AGENT_ID]
                                [--worktree-root WORKTREE_ROOT]
                                agent_id
coordinationhub register: error: the following arguments are required: agent_id
"#,
    },
    Case {
        args: r#"-j list-agents"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub [-h] [-V]
                       {serve,serve-mcp,serve-sse,status,register,heartbeat,deregister,list-agents,agent-relations,acquire-lock,release-lock,refresh-lock,lock-status,list-locks,admin-locks,broadcast,acknowledge-broadcast,wait-for-broadcast-acks,acknowledge-handoff,complete-handoff,cancel-handoff,get-handoffs,wait-for-handoff,wait-for-locks,notify-change,get-notifications,prune-notifications,wait-for-notifications,get-conflicts,contention-hotspots,load-spec,validate-spec,scan-project,dashboard,agent-status,agent-tree,assess,doctor,init,auto-start-dashboard,watch,await-agent,send-message,get-messages,mark-messages-read,create-task,assign-task,update-task-status,query-tasks,create-subtask,retry-task,dead-letter-queue,task-failure-history,wait-for-task,get-available-tasks,declare-work-intent,get-work-intents,clear-work-intent,declare-dependency,manage-dependencies,satisfy-dependency,get-all-dependencies,acquire-coordinator-lease,refresh-coordinator-lease,release-coordinator-lease,get-leader,claim-leadership,leader-status,ha-dashboard,spawn-subagent,report-subagent-spawned,list-pending-spawns,cancel-spawn,request-subagent-deregistration,await-subagent-stopped}
                       ...
coordinationhub: error: unrecognized arguments: -j
"#,
    },
    Case {
        args: r#"heartbeat lute --wobble"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub [-h] [-V]
                       {serve,serve-mcp,serve-sse,status,register,heartbeat,deregister,list-agents,agent-relations,acquire-lock,release-lock,refresh-lock,lock-status,list-locks,admin-locks,broadcast,acknowledge-broadcast,wait-for-broadcast-acks,acknowledge-handoff,complete-handoff,cancel-handoff,get-handoffs,wait-for-handoff,wait-for-locks,notify-change,get-notifications,prune-notifications,wait-for-notifications,get-conflicts,contention-hotspots,load-spec,validate-spec,scan-project,dashboard,agent-status,agent-tree,assess,doctor,init,auto-start-dashboard,watch,await-agent,send-message,get-messages,mark-messages-read,create-task,assign-task,update-task-status,query-tasks,create-subtask,retry-task,dead-letter-queue,task-failure-history,wait-for-task,get-available-tasks,declare-work-intent,get-work-intents,clear-work-intent,declare-dependency,manage-dependencies,satisfy-dependency,get-all-dependencies,acquire-coordinator-lease,refresh-coordinator-lease,release-coordinator-lease,get-leader,claim-leadership,leader-status,ha-dashboard,spawn-subagent,report-subagent-spawned,list-pending-spawns,cancel-spawn,request-subagent-deregistration,await-subagent-stopped}
                       ...
coordinationhub: error: unrecognized arguments: --wobble
"#,
    },
    Case {
        args: r#"list-agents -x"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub [-h] [-V]
                       {serve,serve-mcp,serve-sse,status,register,heartbeat,deregister,list-agents,agent-relations,acquire-lock,release-lock,refresh-lock,lock-status,list-locks,admin-locks,broadcast,acknowledge-broadcast,wait-for-broadcast-acks,acknowledge-handoff,complete-handoff,cancel-handoff,get-handoffs,wait-for-handoff,wait-for-locks,notify-change,get-notifications,prune-notifications,wait-for-notifications,get-conflicts,contention-hotspots,load-spec,validate-spec,scan-project,dashboard,agent-status,agent-tree,assess,doctor,init,auto-start-dashboard,watch,await-agent,send-message,get-messages,mark-messages-read,create-task,assign-task,update-task-status,query-tasks,create-subtask,retry-task,dead-letter-queue,task-failure-history,wait-for-task,get-available-tasks,declare-work-intent,get-work-intents,clear-work-intent,declare-dependency,manage-dependencies,satisfy-dependency,get-all-dependencies,acquire-coordinator-lease,refresh-coordinator-lease,release-coordinator-lease,get-leader,claim-leadership,leader-status,ha-dashboard,spawn-subagent,report-subagent-spawned,list-pending-spawns,cancel-spawn,request-subagent-deregistration,await-subagent-stopped}
                       ...
coordinationhub: error: unrecognized arguments: -x
"#,
    },
    Case {
        args: r#"register lute extra-pos"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub [-h] [-V]
                       {serve,serve-mcp,serve-sse,status,register,heartbeat,deregister,list-agents,agent-relations,acquire-lock,release-lock,refresh-lock,lock-status,list-locks,admin-locks,broadcast,acknowledge-broadcast,wait-for-broadcast-acks,acknowledge-handoff,complete-handoff,cancel-handoff,get-handoffs,wait-for-handoff,wait-for-locks,notify-change,get-notifications,prune-notifications,wait-for-notifications,get-conflicts,contention-hotspots,load-spec,validate-spec,scan-project,dashboard,agent-status,agent-tree,assess,doctor,init,auto-start-dashboard,watch,await-agent,send-message,get-messages,mark-messages-read,create-task,assign-task,update-task-status,query-tasks,create-subtask,retry-task,dead-letter-queue,task-failure-history,wait-for-task,get-available-tasks,declare-work-intent,get-work-intents,clear-work-intent,declare-dependency,manage-dependencies,satisfy-dependency,get-all-dependencies,acquire-coordinator-lease,refresh-coordinator-lease,release-coordinator-lease,get-leader,claim-leadership,leader-status,ha-dashboard,spawn-subagent,report-subagent-spawned,list-pending-spawns,cancel-spawn,request-subagent-deregistration,await-subagent-stopped}
                       ...
coordinationhub: error: unrecognized arguments: extra-pos
"#,
    },
    Case {
        args: r#"acquire-lock m/z.c lute --re"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub acquire-lock [-h] [--storage-dir STORAGE_DIR]
                                    [--project-root PROJECT_ROOT]
                                    [--namespace NAMESPACE] [-j] [--replica]
                                    [--lock-type {exclusive,shared}]
                                    [--ttl TTL] [--force]
                                    [--region-start REGION_START]
                                    [--region-end REGION_END] [--retry]
                                    [--max-retries MAX_RETRIES]
                                    [--backoff-ms BACKOFF_MS]
                                    [--timeout-ms TIMEOUT_MS]
                                    document_path agent_id
coordinationhub acquire-lock: error: ambiguous option: --re could match --replica, --region-start, --region-end, --retry
"#,
    },
    Case {
        args: r#"heartbeat lute --json=yes"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub heartbeat [-h] [--storage-dir STORAGE_DIR]
                                 [--project-root PROJECT_ROOT]
                                 [--namespace NAMESPACE] [-j] [--replica]
                                 agent_id
coordinationhub heartbeat: error: argument -j/--json: ignored explicit argument 'yes'
"#,
    },
    Case {
        args: r#"prune-notifications --max-a 5"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub prune-notifications [-h] [--storage-dir STORAGE_DIR]
                                           [--project-root PROJECT_ROOT]
                                           [--namespace NAMESPACE] [-j]
                                           [--replica]
                                           [--max-age-seconds MAX_AGE_SECONDS]
                                           [--max-entries MAX_ENTRIES]
coordinationhub prune-notifications: error: ambiguous option: --max-a could match --max-age-seconds, --max-age
"#,
    },
    Case {
        args: r#"wait-for-locks lute --timeout 1"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub wait-for-locks [-h] [--storage-dir STORAGE_DIR]
                                      [--project-root PROJECT_ROOT]
                                      [--namespace NAMESPACE] [-j] [--replica]
                                      [--timeout TIMEOUT]
                                      agent_id document_paths
                                      [document_paths ...]
coordinationhub wait-for-locks: error: the following arguments are required: document_paths
"#,
    },
    Case {
        args: r#"satisfy-dependency one"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub satisfy-dependency [-h] [--storage-dir STORAGE_DIR]
                                          [--project-root PROJECT_ROOT]
                                          [--namespace NAMESPACE] [-j]
                                          [--replica]
                                          dep_id
coordinationhub satisfy-dependency: error: argument dep_id: invalid int value: 'one'
"#,
    },
    Case {
        args: r#"bogus-command"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub [-h] [-V]
                       {serve,serve-mcp,serve-sse,status,register,heartbeat,deregister,list-agents,agent-relations,acquire-lock,release-lock,refresh-lock,lock-status,list-locks,admin-locks,broadcast,acknowledge-broadcast,wait-for-broadcast-acks,acknowledge-handoff,complete-handoff,cancel-handoff,get-handoffs,wait-for-handoff,wait-for-locks,notify-change,get-notifications,prune-notifications,wait-for-notifications,get-conflicts,contention-hotspots,load-spec,validate-spec,scan-project,dashboard,agent-status,agent-tree,assess,doctor,init,auto-start-dashboard,watch,await-agent,send-message,get-messages,mark-messages-read,create-task,assign-task,update-task-status,query-tasks,create-subtask,retry-task,dead-letter-queue,task-failure-history,wait-for-task,get-available-tasks,declare-work-intent,get-work-intents,clear-work-intent,declare-dependency,manage-dependencies,satisfy-dependency,get-all-dependencies,acquire-coordinator-lease,refresh-coordinator-lease,release-coordinator-lease,get-leader,claim-leadership,leader-status,ha-dashboard,spawn-subagent,report-subagent-spawned,list-pending-spawns,cancel-spawn,request-subagent-deregistration,await-subagent-stopped}
                       ...
coordinationhub: error: argument command: invalid choice: 'bogus-command' (choose from serve, serve-mcp, serve-sse, status, register, heartbeat, deregister, list-agents, agent-relations, acquire-lock, release-lock, refresh-lock, lock-status, list-locks, admin-locks, broadcast, acknowledge-broadcast, wait-for-broadcast-acks, acknowledge-handoff, complete-handoff, cancel-handoff, get-handoffs, wait-for-handoff, wait-for-locks, notify-change, get-notifications, prune-notifications, wait-for-notifications, get-conflicts, contention-hotspots, load-spec, validate-spec, scan-project, dashboard, agent-status, agent-tree, assess, doctor, init, auto-start-dashboard, watch, await-agent, send-message, get-messages, mark-messages-read, create-task, assign-task, update-task-status, query-tasks, create-subtask, retry-task, dead-letter-queue, task-failure-history, wait-for-task, get-available-tasks, declare-work-intent, get-work-intents, clear-work-intent, declare-dependency, manage-dependencies, satisfy-dependency, get-all-dependencies, acquire-coordinator-lease, refresh-coordinator-lease, release-coordinator-lease, get-leader, claim-leadership, leader-status, ha-dashboard, spawn-subagent, report-subagent-spawned, list-pending-spawns, cancel-spawn, request-subagent-deregistration, await-subagent-stopped)
"#,
    },
    Case {
        args: r#"status --storage-dir"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub status [-h] [--storage-dir STORAGE_DIR]
                              [--project-root PROJECT_ROOT]
                              [--namespace NAMESPACE] [-j] [--replica]
coordinationhub status: error: argument --storage-dir: expected one argument
"#,
    },
    Case {
        args: r#"heartbeat --help"#,
        columns: None,
        rc: 0,
        stdout: r#"usage: coordinationhub heartbeat [-h] [--storage-dir STORAGE_DIR]
                                 [--project-root PROJECT_ROOT]
                                 [--namespace NAMESPACE] [-j] [--replica]
                                 agent_id

positional arguments:
  agent_id              Agent identifier

options:
  -h, --help            show this help message and exit
  --storage-dir STORAGE_DIR
  --project-root PROJECT_ROOT
  --namespace NAMESPACE
  -j, --json            Emit structured JSON output instead of text
  --replica             Use read-replica mode (direct WAL read, no writer
                        round-trip)
"#,
        stderr: r#""#,
    },
    Case {
        args: r#"heartbeat --help"#,
        columns: Some("60"),
        rc: 0,
        stdout: r#"usage: coordinationhub heartbeat [-h]
                                 [--storage-dir STORAGE_DIR]
                                 [--project-root PROJECT_ROOT]
                                 [--namespace NAMESPACE]
                                 [-j] [--replica]
                                 agent_id

positional arguments:
  agent_id              Agent identifier

options:
  -h, --help            show this help message and exit
  --storage-dir STORAGE_DIR
  --project-root PROJECT_ROOT
  --namespace NAMESPACE
  -j, --json            Emit structured JSON output
                        instead of text
  --replica             Use read-replica mode (direct WAL
                        read, no writer round-trip)
"#,
        stderr: r#""#,
    },
    Case {
        args: r#"heartbeat --help"#,
        columns: Some("120"),
        rc: 0,
        stdout: r#"usage: coordinationhub heartbeat [-h] [--storage-dir STORAGE_DIR] [--project-root PROJECT_ROOT]
                                 [--namespace NAMESPACE] [-j] [--replica]
                                 agent_id

positional arguments:
  agent_id              Agent identifier

options:
  -h, --help            show this help message and exit
  --storage-dir STORAGE_DIR
  --project-root PROJECT_ROOT
  --namespace NAMESPACE
  -j, --json            Emit structured JSON output instead of text
  --replica             Use read-replica mode (direct WAL read, no writer round-trip)
"#,
        stderr: r#""#,
    },
    Case {
        args: r#"acquire-lock --help"#,
        columns: None,
        rc: 0,
        stdout: r#"usage: coordinationhub acquire-lock [-h] [--storage-dir STORAGE_DIR]
                                    [--project-root PROJECT_ROOT]
                                    [--namespace NAMESPACE] [-j] [--replica]
                                    [--lock-type {exclusive,shared}]
                                    [--ttl TTL] [--force]
                                    [--region-start REGION_START]
                                    [--region-end REGION_END] [--retry]
                                    [--max-retries MAX_RETRIES]
                                    [--backoff-ms BACKOFF_MS]
                                    [--timeout-ms TIMEOUT_MS]
                                    document_path agent_id

positional arguments:
  document_path         Path to the document
  agent_id              Agent requesting the lock

options:
  -h, --help            show this help message and exit
  --storage-dir STORAGE_DIR
  --project-root PROJECT_ROOT
  --namespace NAMESPACE
  -j, --json            Emit structured JSON output instead of text
  --replica             Use read-replica mode (direct WAL read, no writer
                        round-trip)
  --lock-type {exclusive,shared}
  --ttl TTL
  --force
  --region-start REGION_START
                        Start line for region lock
  --region-end REGION_END
                        End line for region lock
  --retry               Retry with exponential backoff on contention
  --max-retries MAX_RETRIES
                        Maximum retry attempts (default: 5)
  --backoff-ms BACKOFF_MS
                        Starting backoff in MILLISECONDS (default: 100ms).
                        Exponential-backoff ladder between retries.
  --timeout-ms TIMEOUT_MS
                        Total retry timeout in MILLISECONDS (default: 5000ms =
                        5s). Abandons the acquire when exceeded.
"#,
        stderr: r#""#,
    },
    Case {
        args: r#"--help"#,
        columns: None,
        rc: 0,
        stdout: r#"usage: coordinationhub [-h] [-V]
                       {serve,serve-mcp,serve-sse,status,register,heartbeat,deregister,list-agents,agent-relations,acquire-lock,release-lock,refresh-lock,lock-status,list-locks,admin-locks,broadcast,acknowledge-broadcast,wait-for-broadcast-acks,acknowledge-handoff,complete-handoff,cancel-handoff,get-handoffs,wait-for-handoff,wait-for-locks,notify-change,get-notifications,prune-notifications,wait-for-notifications,get-conflicts,contention-hotspots,load-spec,validate-spec,scan-project,dashboard,agent-status,agent-tree,assess,doctor,init,auto-start-dashboard,watch,await-agent,send-message,get-messages,mark-messages-read,create-task,assign-task,update-task-status,query-tasks,create-subtask,retry-task,dead-letter-queue,task-failure-history,wait-for-task,get-available-tasks,declare-work-intent,get-work-intents,clear-work-intent,declare-dependency,manage-dependencies,satisfy-dependency,get-all-dependencies,acquire-coordinator-lease,refresh-coordinator-lease,release-coordinator-lease,get-leader,claim-leadership,leader-status,ha-dashboard,spawn-subagent,report-subagent-spawned,list-pending-spawns,cancel-spawn,request-subagent-deregistration,await-subagent-stopped}
                       ...

CoordinationHub — declarative multi-agent coordination

positional arguments:
  {serve,serve-mcp,serve-sse,status,register,heartbeat,deregister,list-agents,agent-relations,acquire-lock,release-lock,refresh-lock,lock-status,list-locks,admin-locks,broadcast,acknowledge-broadcast,wait-for-broadcast-acks,acknowledge-handoff,complete-handoff,cancel-handoff,get-handoffs,wait-for-handoff,wait-for-locks,notify-change,get-notifications,prune-notifications,wait-for-notifications,get-conflicts,contention-hotspots,load-spec,validate-spec,scan-project,dashboard,agent-status,agent-tree,assess,doctor,init,auto-start-dashboard,watch,await-agent,send-message,get-messages,mark-messages-read,create-task,assign-task,update-task-status,query-tasks,create-subtask,retry-task,dead-letter-queue,task-failure-history,wait-for-task,get-available-tasks,declare-work-intent,get-work-intents,clear-work-intent,declare-dependency,manage-dependencies,satisfy-dependency,get-all-dependencies,acquire-coordinator-lease,refresh-coordinator-lease,release-coordinator-lease,get-leader,claim-leadership,leader-status,ha-dashboard,spawn-subagent,report-subagent-spawned,list-pending-spawns,cancel-spawn,request-subagent-deregistration,await-subagent-stopped}
    serve               Start HTTP MCP server
    serve-mcp           Start MCP server (stdio mode)
    serve-sse           Start HTTP server with SSE dashboard
    status              Get coordination system status summary
    register            Register an agent and get context bundle
    heartbeat           Send agent heartbeat
    deregister          Deregister an agent
    list-agents         List registered agents
    agent-relations     Get agent lineage or siblings
    acquire-lock        Acquire a document lock
    release-lock        Release a held lock
    refresh-lock        Extend a lock's TTL
    lock-status         Check if a document is locked
    list-locks          List all active locks
    admin-locks         Administrative lock operations
    broadcast           Announce intention to siblings
    acknowledge-broadcast
                        Acknowledge receipt of a broadcast
    wait-for-broadcast-acks
                        Wait until all broadcast acknowledgments are received
    acknowledge-handoff
                        Acknowledge a handoff
    complete-handoff    Complete a handoff
    cancel-handoff      Cancel a handoff
    get-handoffs        Get handoffs
    wait-for-handoff    Wait until a handoff is completed
    wait-for-locks      Poll until locks are released
    notify-change       Record a change event
    get-notifications   Poll for change notifications
    prune-notifications
                        Clean up old notifications
    wait-for-notifications
                        Long-poll for new notifications until one arrives or
                        timeout expires
    get-conflicts       Query the conflict log
    contention-hotspots
                        Rank files by lock contention frequency
    load-spec           Reload coordination spec from disk
    validate-spec       Validate loaded coordination spec
    scan-project        Perform file ownership scan
    dashboard           Print live agent/file status table
    agent-status        Get full status for an agent
    agent-tree          Print agent hierarchy as a tree
    assess              Run an assessment suite or score the live session
    doctor              Validate CoordinationHub setup and diagnose issues
    init                Set up CoordinationHub: create DB, configure hooks
    auto-start-dashboard
                        Idempotently launch the SSE dashboard if not already
                        running
    watch               Live-refresh agent tree (Ctrl+C to stop)
    await-agent         Wait for an agent to complete
    send-message        Send a message to another agent
    get-messages        Get messages for an agent
    mark-messages-read  Mark messages as read
    create-task         Create a new task in the shared registry
    assign-task         Assign a task to an agent
    update-task-status  Update a task's status
    query-tasks         Unified task query
    create-subtask      Create a subtask under an existing parent task
    retry-task          Retry a task from the dead letter queue
    dead-letter-queue   Get all tasks in the dead letter queue
    task-failure-history
                        Get failure history for a task
    wait-for-task       Poll until a task reaches terminal state or timeout
                        expires
    get-available-tasks
                        Get tasks whose dependencies are all satisfied and are
                        not claimed
    declare-work-intent
                        Declare intent to work on a file
    get-work-intents    Get all active work intents
    clear-work-intent   Clear an agent's declared work intent
    declare-dependency  Declare a dependency between agents
    manage-dependencies
                        Unified dependency query
    satisfy-dependency  Mark a dependency as satisfied
    get-all-dependencies
                        List all declared dependencies
    acquire-coordinator-lease
                        Attempt to acquire the coordinator leadership lease
    refresh-coordinator-lease
                        Refresh the coordinator lease TTL
    release-coordinator-lease
                        Release the coordinator leadership lease
    get-leader          Print the current coordinator lease holder
    claim-leadership    Claim coordinator leadership from a failed leader
    leader-status       Print coordinator leader and lease details
    ha-dashboard        HA dashboard: lease state, replica count, agent status
    spawn-subagent      Register intent to spawn a sub-agent
    report-subagent-spawned
                        Report that a sub-agent was spawned by an external
                        system
    list-pending-spawns
                        List pending sub-agent spawn requests for a parent
                        agent
    cancel-spawn        Cancel a pending spawn request
    request-subagent-deregistration
                        Request graceful deregistration of a child agent
    await-subagent-stopped
                        Poll until a child agent is stopped or timeout

options:
  -h, --help            show this help message and exit
  -V, --version         show program's version number and exit
"#,
        stderr: r#""#,
    },
    Case {
        args: r#"--help"#,
        columns: Some("60"),
        rc: 0,
        stdout: r#"usage: coordinationhub [-h] [-V]
                       {serve,serve-mcp,serve-sse,status,register,heartbeat,deregister,list-agents,agent-relations,acquire-lock,release-lock,refresh-lock,lock-status,list-locks,admin-locks,broadcast,acknowledge-broadcast,wait-for-broadcast-acks,acknowledge-handoff,complete-handoff,cancel-handoff,get-handoffs,wait-for-handoff,wait-for-locks,notify-change,get-notifications,prune-notifications,wait-for-notifications,get-conflicts,contention-hotspots,load-spec,validate-spec,scan-project,dashboard,agent-status,agent-tree,assess,doctor,init,auto-start-dashboard,watch,await-agent,send-message,get-messages,mark-messages-read,create-task,assign-task,update-task-status,query-tasks,create-subtask,retry-task,dead-letter-queue,task-failure-history,wait-for-task,get-available-tasks,declare-work-intent,get-work-intents,clear-work-intent,declare-dependency,manage-dependencies,satisfy-dependency,get-all-dependencies,acquire-coordinator-lease,refresh-coordinator-lease,release-coordinator-lease,get-leader,claim-leadership,leader-status,ha-dashboard,spawn-subagent,report-subagent-spawned,list-pending-spawns,cancel-spawn,request-subagent-deregistration,await-subagent-stopped}
                       ...

CoordinationHub — declarative multi-agent coordination

positional arguments:
  {serve,serve-mcp,serve-sse,status,register,heartbeat,deregister,list-agents,agent-relations,acquire-lock,release-lock,refresh-lock,lock-status,list-locks,admin-locks,broadcast,acknowledge-broadcast,wait-for-broadcast-acks,acknowledge-handoff,complete-handoff,cancel-handoff,get-handoffs,wait-for-handoff,wait-for-locks,notify-change,get-notifications,prune-notifications,wait-for-notifications,get-conflicts,contention-hotspots,load-spec,validate-spec,scan-project,dashboard,agent-status,agent-tree,assess,doctor,init,auto-start-dashboard,watch,await-agent,send-message,get-messages,mark-messages-read,create-task,assign-task,update-task-status,query-tasks,create-subtask,retry-task,dead-letter-queue,task-failure-history,wait-for-task,get-available-tasks,declare-work-intent,get-work-intents,clear-work-intent,declare-dependency,manage-dependencies,satisfy-dependency,get-all-dependencies,acquire-coordinator-lease,refresh-coordinator-lease,release-coordinator-lease,get-leader,claim-leadership,leader-status,ha-dashboard,spawn-subagent,report-subagent-spawned,list-pending-spawns,cancel-spawn,request-subagent-deregistration,await-subagent-stopped}
    serve               Start HTTP MCP server
    serve-mcp           Start MCP server (stdio mode)
    serve-sse           Start HTTP server with SSE
                        dashboard
    status              Get coordination system status
                        summary
    register            Register an agent and get context
                        bundle
    heartbeat           Send agent heartbeat
    deregister          Deregister an agent
    list-agents         List registered agents
    agent-relations     Get agent lineage or siblings
    acquire-lock        Acquire a document lock
    release-lock        Release a held lock
    refresh-lock        Extend a lock's TTL
    lock-status         Check if a document is locked
    list-locks          List all active locks
    admin-locks         Administrative lock operations
    broadcast           Announce intention to siblings
    acknowledge-broadcast
                        Acknowledge receipt of a broadcast
    wait-for-broadcast-acks
                        Wait until all broadcast
                        acknowledgments are received
    acknowledge-handoff
                        Acknowledge a handoff
    complete-handoff    Complete a handoff
    cancel-handoff      Cancel a handoff
    get-handoffs        Get handoffs
    wait-for-handoff    Wait until a handoff is completed
    wait-for-locks      Poll until locks are released
    notify-change       Record a change event
    get-notifications   Poll for change notifications
    prune-notifications
                        Clean up old notifications
    wait-for-notifications
                        Long-poll for new notifications
                        until one arrives or timeout
                        expires
    get-conflicts       Query the conflict log
    contention-hotspots
                        Rank files by lock contention
                        frequency
    load-spec           Reload coordination spec from disk
    validate-spec       Validate loaded coordination spec
    scan-project        Perform file ownership scan
    dashboard           Print live agent/file status table
    agent-status        Get full status for an agent
    agent-tree          Print agent hierarchy as a tree
    assess              Run an assessment suite or score
                        the live session
    doctor              Validate CoordinationHub setup and
                        diagnose issues
    init                Set up CoordinationHub: create DB,
                        configure hooks
    auto-start-dashboard
                        Idempotently launch the SSE
                        dashboard if not already running
    watch               Live-refresh agent tree (Ctrl+C to
                        stop)
    await-agent         Wait for an agent to complete
    send-message        Send a message to another agent
    get-messages        Get messages for an agent
    mark-messages-read  Mark messages as read
    create-task         Create a new task in the shared
                        registry
    assign-task         Assign a task to an agent
    update-task-status  Update a task's status
    query-tasks         Unified task query
    create-subtask      Create a subtask under an existing
                        parent task
    retry-task          Retry a task from the dead letter
                        queue
    dead-letter-queue   Get all tasks in the dead letter
                        queue
    task-failure-history
                        Get failure history for a task
    wait-for-task       Poll until a task reaches terminal
                        state or timeout expires
    get-available-tasks
                        Get tasks whose dependencies are
                        all satisfied and are not claimed
    declare-work-intent
                        Declare intent to work on a file
    get-work-intents    Get all active work intents
    clear-work-intent   Clear an agent's declared work
                        intent
    declare-dependency  Declare a dependency between
                        agents
    manage-dependencies
                        Unified dependency query
    satisfy-dependency  Mark a dependency as satisfied
    get-all-dependencies
                        List all declared dependencies
    acquire-coordinator-lease
                        Attempt to acquire the coordinator
                        leadership lease
    refresh-coordinator-lease
                        Refresh the coordinator lease TTL
    release-coordinator-lease
                        Release the coordinator leadership
                        lease
    get-leader          Print the current coordinator
                        lease holder
    claim-leadership    Claim coordinator leadership from
                        a failed leader
    leader-status       Print coordinator leader and lease
                        details
    ha-dashboard        HA dashboard: lease state, replica
                        count, agent status
    spawn-subagent      Register intent to spawn a sub-
                        agent
    report-subagent-spawned
                        Report that a sub-agent was
                        spawned by an external system
    list-pending-spawns
                        List pending sub-agent spawn
                        requests for a parent agent
    cancel-spawn        Cancel a pending spawn request
    request-subagent-deregistration
                        Request graceful deregistration of
                        a child agent
    await-subagent-stopped
                        Poll until a child agent is
                        stopped or timeout

options:
  -h, --help            show this help message and exit
  -V, --version         show program's version number and
                        exit
"#,
        stderr: r#""#,
    },
    Case {
        args: r#"--version"#,
        columns: None,
        rc: 0,
        stdout: r#"coordinationhub 1.2.0
"#,
        stderr: r#""#,
    },
    Case {
        args: r#"-V"#,
        columns: None,
        rc: 0,
        stdout: r#"coordinationhub 1.2.0
"#,
        stderr: r#""#,
    },
    Case {
        args: r#"--vers"#,
        columns: None,
        rc: 0,
        stdout: r#"coordinationhub 1.2.0
"#,
        stderr: r#""#,
    },
    Case {
        args: r#"-V status"#,
        columns: None,
        rc: 0,
        stdout: r#"coordinationhub 1.2.0
"#,
        stderr: r#""#,
    },
    Case {
        args: r#"status -V"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub [-h] [-V]
                       {serve,serve-mcp,serve-sse,status,register,heartbeat,deregister,list-agents,agent-relations,acquire-lock,release-lock,refresh-lock,lock-status,list-locks,admin-locks,broadcast,acknowledge-broadcast,wait-for-broadcast-acks,acknowledge-handoff,complete-handoff,cancel-handoff,get-handoffs,wait-for-handoff,wait-for-locks,notify-change,get-notifications,prune-notifications,wait-for-notifications,get-conflicts,contention-hotspots,load-spec,validate-spec,scan-project,dashboard,agent-status,agent-tree,assess,doctor,init,auto-start-dashboard,watch,await-agent,send-message,get-messages,mark-messages-read,create-task,assign-task,update-task-status,query-tasks,create-subtask,retry-task,dead-letter-queue,task-failure-history,wait-for-task,get-available-tasks,declare-work-intent,get-work-intents,clear-work-intent,declare-dependency,manage-dependencies,satisfy-dependency,get-all-dependencies,acquire-coordinator-lease,refresh-coordinator-lease,release-coordinator-lease,get-leader,claim-leadership,leader-status,ha-dashboard,spawn-subagent,report-subagent-spawned,list-pending-spawns,cancel-spawn,request-subagent-deregistration,await-subagent-stopped}
                       ...
coordinationhub: error: unrecognized arguments: -V
"#,
    },
    Case {
        args: r#"assign-task --help"#,
        columns: None,
        rc: 0,
        stdout: r#"usage: coordinationhub assign-task [-h] [--storage-dir STORAGE_DIR]
                                   [--project-root PROJECT_ROOT]
                                   [--namespace NAMESPACE] [-j] [--replica]
                                   [--force]
                                   task_id assigned_agent_id

positional arguments:
  task_id               Task to assign
  assigned_agent_id     Agent to assign the task to

options:
  -h, --help            show this help message and exit
  --storage-dir STORAGE_DIR
  --project-root PROJECT_ROOT
  --namespace NAMESPACE
  -j, --json            Emit structured JSON output instead of text
  --replica             Use read-replica mode (direct WAL read, no writer
                        round-trip)
  --force               Reassign even if another agent holds the task
"#,
        stderr: r#""#,
    },
    Case {
        args: r#"assign-task t1 --force"#,
        columns: None,
        rc: 2,
        stdout: r#""#,
        stderr: r#"usage: coordinationhub assign-task [-h] [--storage-dir STORAGE_DIR]
                                   [--project-root PROJECT_ROOT]
                                   [--namespace NAMESPACE] [-j] [--replica]
                                   [--force]
                                   task_id assigned_agent_id
coordinationhub assign-task: error: the following arguments are required: assigned_agent_id
"#,
    },
];

#[test]
fn argparse_errors_and_help_match_python_0_8_0() {
    let mut failures = Vec::new();
    for case in CASES {
        let hub = common::Hub::new("argparse");
        hub.run(&["register", "viola"]);
        hub.run(&["register", "lute", "--parent-id", "viola"]);
        let args = common::split(case.args);
        let out = hub.run_bare(&args, case.columns);
        if (out.code, out.stdout.as_str(), out.stderr.as_str()) != (case.rc, case.stdout, case.stderr) {
            failures.push(format!(
                "{} (COLUMNS {:?}): rc {} want {}\nstdout:\n{}\nwant:\n{}\nstderr:\n{}\nwant:\n{}",
                case.args, case.columns, out.code, case.rc, out.stdout, case.stdout, out.stderr, case.stderr
            ));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n\n"));
}
