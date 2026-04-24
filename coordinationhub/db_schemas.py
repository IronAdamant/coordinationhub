"""Canonical SQLite schema definitions for CoordinationHub.

Pure data — table ``CREATE TABLE IF NOT EXISTS`` statements and the matching
``CREATE INDEX IF NOT EXISTS`` list.  Imported by :mod:`db_migrations`
(for schema re-use inside migration steps) and by :mod:`db` (for the
fresh-install path in ``init_schema``).
"""

from __future__ import annotations


_SCHEMAS = {
    "agents": """
        CREATE TABLE IF NOT EXISTS agents (
            agent_id        TEXT PRIMARY KEY,
            parent_id       TEXT,
            worktree_root   TEXT NOT NULL,
            pid             INTEGER,
            started_at      REAL NOT NULL,
            last_heartbeat  REAL NOT NULL,
            status          TEXT DEFAULT 'active',
            raw_ide_id      TEXT,
            ide_vendor      TEXT
        )
    """,
    "lineage": """
        CREATE TABLE IF NOT EXISTS lineage (
            parent_id  TEXT NOT NULL,
            child_id    TEXT NOT NULL,
            spawned_at REAL NOT NULL,
            PRIMARY KEY (parent_id, child_id)
        )
    """,
    "document_locks": """
        CREATE TABLE IF NOT EXISTS document_locks (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            document_path  TEXT NOT NULL,
            locked_by      TEXT NOT NULL,
            locked_at      REAL NOT NULL,
            lock_ttl       REAL DEFAULT 300.0,
            lock_type      TEXT DEFAULT 'exclusive',
            region_start   INTEGER,
            region_end     INTEGER,
            worktree_root  TEXT
        )
    """,
    "lock_conflicts": """
        CREATE TABLE IF NOT EXISTS lock_conflicts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            document_path TEXT NOT NULL,
            agent_a       TEXT NOT NULL,
            agent_b       TEXT NOT NULL,
            conflict_type TEXT NOT NULL,
            resolution    TEXT DEFAULT 'rejected',
            details_json  TEXT,
            created_at    REAL NOT NULL
        )
    """,
    "change_notifications": """
        CREATE TABLE IF NOT EXISTS change_notifications (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            document_path TEXT NOT NULL,
            change_type   TEXT NOT NULL,
            agent_id      TEXT NOT NULL,
            worktree_root TEXT,
            created_at    REAL NOT NULL
        )
    """,
    "agent_responsibilities": """
        CREATE TABLE IF NOT EXISTS agent_responsibilities (
            agent_id        TEXT PRIMARY KEY,
            graph_agent_id  TEXT,
            role            TEXT,
            model           TEXT,
            responsibilities TEXT,
            current_task    TEXT,
            scope           TEXT,
            updated_at      REAL NOT NULL
        )
    """,
    "pending_tasks": """
        CREATE TABLE IF NOT EXISTS pending_tasks (
            task_id         TEXT PRIMARY KEY,
            scope_id        TEXT NOT NULL,
            subagent_type   TEXT,
            description     TEXT,
            prompt          TEXT,
            created_at      REAL NOT NULL,
            consumed_at     REAL,
            status          TEXT DEFAULT 'pending',
            source          TEXT DEFAULT 'external'
        )
    """,

    "file_ownership": """
        CREATE TABLE IF NOT EXISTS file_ownership (
            document_path     TEXT PRIMARY KEY,
            assigned_agent_id TEXT NOT NULL,
            assigned_at      REAL NOT NULL,
            last_claimed_by  TEXT,
            task_description TEXT
        )
    """,
    "assessment_results": """
        CREATE TABLE IF NOT EXISTS assessment_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            suite_name      TEXT NOT NULL,
            metric          TEXT NOT NULL,
            score           REAL NOT NULL,
            details_json    TEXT,
            run_at          REAL NOT NULL
        )
    """,
    "descendant_registry": """
        CREATE TABLE IF NOT EXISTS descendant_registry (
            ancestor_id   TEXT NOT NULL,
            descendant_id TEXT NOT NULL,
            depth         INTEGER NOT NULL DEFAULT 1,
            registered_at REAL NOT NULL,
            PRIMARY KEY (ancestor_id, descendant_id)
        )
    """,
    "messages": """
        CREATE TABLE IF NOT EXISTS messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent_id TEXT NOT NULL,
            to_agent_id   TEXT NOT NULL,
            message_type  TEXT NOT NULL,
            payload_json  TEXT,
            created_at    REAL NOT NULL,
            read_at       REAL
        )
    """,
    "tasks": """
        CREATE TABLE IF NOT EXISTS tasks (
            id               TEXT PRIMARY KEY,
            parent_agent_id  TEXT NOT NULL,
            parent_task_id   TEXT,
            assigned_agent_id TEXT,
            description      TEXT NOT NULL,
            status           TEXT DEFAULT 'pending',
            created_at       REAL NOT NULL,
            updated_at       REAL NOT NULL,
            depends_on       TEXT DEFAULT '[]',
            blocked_by       TEXT,
            summary          TEXT,
            priority         INTEGER DEFAULT 0,
            error            TEXT
        )
    """,
    "work_intent": """
        CREATE TABLE IF NOT EXISTS work_intent (
            agent_id      TEXT NOT NULL,
            document_path TEXT NOT NULL,
            intent        TEXT NOT NULL,
            declared_at   REAL NOT NULL,
            ttl           REAL DEFAULT 60.0,
            PRIMARY KEY (agent_id, document_path)
        )
    """,
    "handoffs": """
        CREATE TABLE IF NOT EXISTS handoffs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent_id   TEXT NOT NULL,
            to_agents       TEXT NOT NULL,
            document_path   TEXT,
            handoff_type    TEXT DEFAULT 'scope_transfer',
            status          TEXT DEFAULT 'pending',
            created_at     REAL NOT NULL,
            acknowledged_at REAL,
            completed_at    REAL
        )
    """,
    "handoff_acks": """
        CREATE TABLE IF NOT EXISTS handoff_acks (
            handoff_id      INTEGER NOT NULL,
            agent_id        TEXT NOT NULL,
            acknowledged_at REAL NOT NULL,
            PRIMARY KEY (handoff_id, agent_id)
        )
    """,
    "task_failures": """
        CREATE TABLE IF NOT EXISTS task_failures (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id          TEXT NOT NULL,
            error            TEXT,
            attempt          INTEGER NOT NULL DEFAULT 1,
            max_retries      INTEGER NOT NULL DEFAULT 3,
            first_attempt_at REAL NOT NULL,
            last_attempt_at  REAL NOT NULL,
            dead_letter_at   REAL,
            status           TEXT DEFAULT 'failed'
        )
    """,
    "coordinator_leases": """
        CREATE TABLE IF NOT EXISTS coordinator_leases (
            lease_name    TEXT PRIMARY KEY,
            holder_id     TEXT NOT NULL,
            acquired_at   REAL NOT NULL,
            ttl           REAL NOT NULL,
            expires_at    REAL NOT NULL
        )
    """,
    "agent_dependencies": """
        CREATE TABLE IF NOT EXISTS agent_dependencies (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            dependent_agent_id  TEXT NOT NULL,
            depends_on_agent_id TEXT NOT NULL,
            depends_on_task_id  TEXT,
            condition           TEXT DEFAULT 'task_completed',
            satisfied           INTEGER DEFAULT 0,
            satisfied_at        REAL,
            created_at          REAL NOT NULL
        )
    """,
    "pending_spawner_tasks": """
        CREATE TABLE IF NOT EXISTS pending_spawner_tasks (
            id               TEXT PRIMARY KEY,
            parent_agent_id  TEXT NOT NULL,
            subagent_type    TEXT,
            description      TEXT,
            prompt           TEXT,
            created_at       REAL NOT NULL,
            consumed_at      REAL,
            status           TEXT DEFAULT 'pending',
            source           TEXT DEFAULT 'external'
        )
    """,
    "broadcasts": """
        CREATE TABLE IF NOT EXISTS broadcasts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent_id   TEXT NOT NULL,
            document_path   TEXT,
            message         TEXT,
            created_at      REAL NOT NULL,
            ttl             REAL DEFAULT 30.0,
            expires_at      REAL NOT NULL,
            expected_count  INTEGER DEFAULT 0
        )
    """,
    "broadcast_acks": """
        CREATE TABLE IF NOT EXISTS broadcast_acks (
            broadcast_id    INTEGER NOT NULL,
            agent_id        TEXT NOT NULL,
            acknowledged_at REAL NOT NULL,
            PRIMARY KEY (broadcast_id, agent_id)
        )
    """,
    "broadcast_targets": """
        CREATE TABLE IF NOT EXISTS broadcast_targets (
            broadcast_id    INTEGER NOT NULL,
            agent_id        TEXT NOT NULL,
            PRIMARY KEY (broadcast_id, agent_id)
        )
    """,
    "coordination_events": """
        CREATE TABLE IF NOT EXISTS coordination_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            topic           TEXT NOT NULL,
            payload_json    TEXT NOT NULL,
            created_at      REAL NOT NULL
        )
    """,

}

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status)",
    "CREATE INDEX IF NOT EXISTS idx_agents_parent ON agents(parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_lineage_child ON lineage(child_id)",
    "CREATE INDEX IF NOT EXISTS idx_lineage_parent ON lineage(parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_locks_path ON document_locks(document_path)",
    "CREATE INDEX IF NOT EXISTS idx_locks_locked_by ON document_locks(locked_by)",
    "CREATE INDEX IF NOT EXISTS idx_conflicts_doc ON lock_conflicts(document_path)",
    "CREATE INDEX IF NOT EXISTS idx_conflicts_time ON lock_conflicts(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_notif_time ON change_notifications(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_notif_agent ON change_notifications(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_file_owner_agent ON file_ownership(assigned_agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_locks_expiry ON document_locks(document_path, locked_at, lock_ttl)",
    "CREATE INDEX IF NOT EXISTS idx_agents_raw_ide_id ON agents(raw_ide_id)",
    "CREATE INDEX IF NOT EXISTS idx_pending_tasks_scope_type ON pending_tasks(scope_id, subagent_type, status)",
    "CREATE INDEX IF NOT EXISTS idx_descendant_ancestor ON descendant_registry(ancestor_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_parent_task ON tasks(parent_task_id)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority DESC, created_at ASC)",
    "CREATE INDEX IF NOT EXISTS idx_handoffs_from ON handoffs(from_agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_handoffs_status ON handoffs(status)",
    "CREATE INDEX IF NOT EXISTS idx_handoff_acks_id ON handoff_acks(handoff_id)",
    "CREATE INDEX IF NOT EXISTS idx_deps_dependent ON agent_dependencies(dependent_agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_deps_depends_on ON agent_dependencies(depends_on_agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_deps_satisfied ON agent_dependencies(satisfied)",
    "CREATE INDEX IF NOT EXISTS idx_task_failures_task ON task_failures(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_failures_status ON task_failures(status)",
    "CREATE INDEX IF NOT EXISTS idx_leases_expires ON coordinator_leases(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_pending_tasks_created_at ON pending_tasks(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_broadcasts_from ON broadcasts(from_agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_broadcasts_expires ON broadcasts(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_broadcast_acks_id ON broadcast_acks(broadcast_id)",
    "CREATE INDEX IF NOT EXISTS idx_coordination_events_topic ON coordination_events(topic, created_at)",
    # T4.6: read-accelerating indexes.
    # - messages(to_agent_id, read_at) covers unread_only=True scans.
    # - coordination_events(created_at) alone powers prune by age.
    "CREATE INDEX IF NOT EXISTS idx_messages_to_read ON messages(to_agent_id, read_at)",
    "CREATE INDEX IF NOT EXISTS idx_coordination_events_created ON coordination_events(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_assessment_results_run_at ON assessment_results(run_at)",
    "CREATE INDEX IF NOT EXISTS idx_agents_status_heartbeat ON agents(status, last_heartbeat)",
]
