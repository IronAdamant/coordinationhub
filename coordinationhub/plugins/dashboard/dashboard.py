"""Web dashboard for CoordinationHub — zero external dependencies.

Provides a self-contained HTML dashboard that polls API endpoints
and renders agent trees, task boards, work intents, and handoffs
using pure SVG (no Mermaid, no D3, no CDN).

Usage:
    from .dashboard import get_dashboard_data, DASHBOARD_HTML

    # In MCP server:
    if self.path == "/":
        self._serve_dashboard()
    elif self.path.startswith("/api/"):
        self._serve_api(self.path)

    # Get aggregated data for API endpoints:
    data = get_dashboard_data(engine.connect)
"""

from __future__ import annotations

from typing import Any, Callable

# Type alias for the connect function passed by callers
ConnectFn = Callable[[], Any]


# ------------------------------------------------------------------ #
# Data aggregation
# ------------------------------------------------------------------ #

def get_dashboard_data(connect: ConnectFn) -> dict[str, Any]:
    """Aggregate all tables into a single dict for the dashboard.

    Returns:
        {
            "agents": [...],
            "tasks": [...],
            "work_intents": [...],
            "handoffs": [...],
            "dependencies": [...],
            "locks": [...],
        }
    """
    conn = connect()

    def _dict(rows, key=None):
        if key is None:
            return [dict(r) for r in rows]
        return {dict(r)[key]: dict(r) for r in rows}

    return {
        "agents": _dict(conn.execute(
            """
            SELECT a.*, r.current_task, r.role, r.graph_agent_id
            FROM agents a
            LEFT JOIN agent_responsibilities r ON r.agent_id = a.agent_id
            WHERE a.status != 'stopped'
            ORDER BY a.started_at
            """
        ).fetchall()),
        "tasks": _dict(conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC"
        ).fetchall()),
        "work_intents": _dict(conn.execute(
            "SELECT * FROM work_intent ORDER BY declared_at DESC"
        ).fetchall()),
        "handoffs": _dict(conn.execute(
            "SELECT * FROM handoffs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()),
        "dependencies": _dict(conn.execute(
            "SELECT * FROM agent_dependencies ORDER BY created_at DESC"
        ).fetchall()),
        "locks": _dict(conn.execute(
            """
            SELECT l.*, o.assigned_agent_id AS owner_agent_id
            FROM document_locks l
            LEFT JOIN file_ownership o ON o.document_path = l.document_path
            ORDER BY l.locked_at DESC
            """
        ).fetchall()),
    }


# ------------------------------------------------------------------ #
# Self-contained HTML dashboard
# ------------------------------------------------------------------ #

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CoordinationHub — Agent Swarm Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f1117; color: #e6e8ed; min-height: 100vh; }
  header { background: #1a1d27; border-bottom: 1px solid #2d3148; padding: 12px 24px; display: flex; justify-content: space-between; align-items: center; gap: 16px; }
  header .brand h1 { font-size: 18px; font-weight: 600; color: #8b5cf6; }
  header .brand p { font-size: 12px; color: #94a3b8; margin-top: 2px; }
  header .status { font-size: 12px; color: #6b7280; text-align: right; }
  .dashboard { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px; }
  .panel { background: #1a1d27; border: 1px solid #2d3148; border-radius: 8px; padding: 16px; }
  .panel h2 { font-size: 13px; font-weight: 600; color: #8b5cf6; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
  .panel .panel-blurb { font-size: 12px; color: #6b7280; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid #2d3148; }
  .panel.full-width { grid-column: 1 / -1; }
  .legend { font-size: 11px; color: #6b7280; display: inline-flex; gap: 10px; flex-wrap: wrap; margin-left: 8px; }
  .legend .chip { display: inline-block; padding: 1px 6px; border-radius: 8px; font-size: 10px; }

  /* Agent Tree */
  .agent-tree-container { height: 340px; overflow: auto; }
  .agent-tree-container svg { display: block; }

  /* Task Board */
  .task-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .task-table th { text-align: left; color: #6b7280; font-weight: 500; padding: 6px 8px; border-bottom: 1px solid #2d3148; }
  .task-table td { padding: 6px 8px; border-bottom: 1px solid #1e212d; }
  .task-table tr:hover { background: #1e212d; }
  .status-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .status-pending { background: #1e293b; color: #94a3b8; }
  .status-in_progress { background: #1c2b1e; color: #4ade80; }
  .status-completed { background: #1c2b1e; color: #4ade80; border: 1px solid #2d5a36; }
  .status-blocked { background: #2d1e1e; color: #f87171; }
  .status-unknown { background: #1e293b; color: #94a3b8; }

  /* Work Intents */
  .intent-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }
  .intent-card { background: #1e212d; border: 1px solid #2d3148; border-radius: 6px; padding: 10px; font-size: 12px; }
  .intent-card .intent-agent { color: #8b5cf6; font-weight: 600; }
  .intent-card .intent-file { color: #94a3b8; margin-top: 4px; font-family: monospace; }
  .intent-card .intent-desc { color: #e6e8ed; margin-top: 4px; }
  .intent-card .intent-ttl { color: #6b7280; font-size: 11px; margin-top: 4px; }

  /* Handoffs */
  .handoff-list { display: flex; flex-direction: column; gap: 8px; }
  .handoff-item { background: #1e212d; border: 1px solid #2d3148; border-radius: 6px; padding: 10px; font-size: 12px; }
  .handoff-item .handoff-id { color: #8b5cf6; font-weight: 600; }
  .handoff-item .handoff-route { color: #94a3b8; margin-top: 4px; }
  .handoff-item .handoff-status { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; margin-top: 4px; }
  .handoff-pending { background: #1e293b; color: #94a3b8; }
  .handoff-acknowledged { background: #1c2b1e; color: #4ade80; }
  .handoff-completed { background: #1c2b1e; color: #4ade80; border: 1px solid #2d5a36; }
  .handoff-cancelled { background: #2d1e1e; color: #f87171; }

  /* Dependencies */
  .dep-list { display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
  .dep-item { background: #1e212d; border: 1px solid #2d3148; border-radius: 6px; padding: 8px 10px; display: flex; align-items: center; gap: 8px; }
  .dep-item .dep-from { color: #f87171; font-weight: 600; }
  .dep-item .dep-arrow { color: #6b7280; }
  .dep-item .dep-to { color: #4ade80; font-weight: 600; }
  .dep-item .dep-condition { color: #6b7280; font-size: 11px; }
  .dep-item .dep-sat { font-weight: 600; }
  .dep-sat-yes { color: #4ade80; }
  .dep-sat-no { color: #f87171; }

  /* Locks */
  .lock-list { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
  .lock-item { background: #1e212d; border: 1px solid #2d3148; border-radius: 4px; padding: 6px 8px; font-family: monospace; display: flex; justify-content: space-between; }
  .lock-item .lock-path { color: #94a3b8; }
  .lock-item .lock-agent { color: #8b5cf6; font-size: 11px; }
  .lock-item .lock-type { font-size: 11px; padding: 1px 6px; border-radius: 8px; }
  .lock-exclusive { background: #2d1e1e; color: #f87171; }
  .lock-shared { background: #1e293b; color: #94a3b8; }

  /* Empty states */
  .empty { color: #4b5563; font-size: 12px; font-style: italic; margin-top: 8px; }
  .timestamp { font-size: 11px; color: #4b5563; margin-top: 8px; }

  /* SVG node styles */
  .agent-node rect { fill: #1e212d; stroke: #2d3148; stroke-width: 1.5; rx: 6; }
  .agent-node text { fill: #e6e8ed; font-size: 12px; font-family: -apple-system, sans-serif; }
  .agent-node .status-dot { stroke: none; }
  .agent-node .status-active { fill: #4ade80; }
  .agent-node .status-stopped { fill: #6b7280; }
  .agent-node .status-unknown { fill: #f87171; }
  .agent-edge { stroke: #2d3148; stroke-width: 1.5; fill: none; }
</style>
</head>
<body>
<header>
  <div class="brand">
    <h1>CoordinationHub</h1>
    <p>Live view of the multi-agent swarm working on this project — who is alive, what they are doing, which files they hold, and where they are blocked.</p>
  </div>
  <div class="status" id="timestamp">Loading&hellip;</div>
</header>

<div class="dashboard">
  <!-- Agent Tree -->
  <div class="panel full-width">
    <h2>Agent Tree
      <span class="legend">
        <span><span class="chip" style="background:#1c2b1e;color:#4ade80;">&#9679;</span> active (heartbeat &lt; 10min)</span>
        <span><span class="chip" style="background:#2a2d3a;color:#6b7280;">&#9679;</span> stopped</span>
        <span><span class="chip" style="background:#2d1e1e;color:#f87171;">&#9679;</span> stale / unknown</span>
      </span>
    </h2>
    <div class="panel-blurb">Each box is one agent. Parent&rarr;child edges show spawn lineage. The small grey line under the agent ID is the agent's current task.</div>
    <div class="agent-tree-container" id="agent-tree-container">
      <svg id="agent-tree-svg" width="100%" height="320"></svg>
    </div>
  </div>

  <!-- Task Board -->
  <div class="panel">
    <h2>Task Registry</h2>
    <div class="panel-blurb">Shared work board. Any agent can create a task; an orchestrator assigns it to a worker. Status flows pending &rarr; in_progress &rarr; completed/blocked.</div>
    <table class="task-table" id="task-table">
      <thead><tr>
        <th>Task ID</th><th>Status</th><th>Assigned</th><th>Description</th>
      </tr></thead>
      <tbody id="task-tbody"></tbody>
    </table>
    <div class="timestamp" id="task-timestamp"></div>
  </div>

  <!-- Work Intents -->
  <div class="panel">
    <h2>Work Intent Board</h2>
    <div class="panel-blurb">Soft "I am about to touch this file" signals. Non-binding — agents use them to avoid colliding before acquiring a hard lock.</div>
    <div class="intent-grid" id="intent-grid"></div>
    <div class="timestamp" id="intent-timestamp"></div>
  </div>

  <!-- Handoffs -->
  <div class="panel">
    <h2>Handoffs</h2>
    <div class="panel-blurb">Explicit "I am done with this scope — please take over" events, acknowledged by the receiving agent(s).</div>
    <div class="handoff-list" id="handoff-list"></div>
    <div class="timestamp" id="handoff-timestamp"></div>
  </div>

  <!-- Dependencies -->
  <div class="panel">
    <h2>Agent Dependencies</h2>
    <div class="panel-blurb">"Agent X is waiting on agent Y to finish task T." Red = still blocked, green = satisfied.</div>
    <div class="dep-list" id="dep-list"></div>
    <div class="timestamp" id="dep-timestamp"></div>
  </div>

  <!-- Active Locks -->
  <div class="panel full-width">
    <h2>Active Locks
      <span class="legend">
        <span><span class="chip lock-exclusive">exclusive</span> one writer</span>
        <span><span class="chip lock-shared">shared</span> multiple readers</span>
      </span>
    </h2>
    <div class="panel-blurb">TTL-based file locks. If a lock crosses a file owned by another agent, the table flags it with &#9888; boundary.</div>
    <div class="lock-list" id="lock-list"></div>
    <div class="timestamp" id="lock-timestamp"></div>
  </div>
</div>

<script>
(function() {
  var POLL_INTERVAL = 5000;
  var apiBase = '/api';
  var es = null;
  var useSSE = true;

  function fetchJSON(url) {
    return fetch(url).then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function onDashboardData(data) {
    document.getElementById('timestamp').textContent = 'Last updated: ' + new Date().toLocaleTimeString() + (es ? ' (SSE)' : ' (poll)');
    renderAgentTree(data);
    renderTasks(data);
    renderIntents(data);
    renderHandoffs(data);
    renderDependencies(data);
    renderLocks(data);
  }

  function startSSE() {
    if (es) es.close();
    es = new EventSource('/events');
    es.onmessage = function(evt) {
      try {
        var data = JSON.parse(evt.data);
        onDashboardData(data);
      } catch (e) { /* ignore parse errors */ }
    };
    es.onerror = function() {
      useSSE = false;
      es.close();
      es = null;
      setTimeout(startSSE, 30000);  // retry SSE every 30s
    };
  }

  function poll() {
    fetchJSON(apiBase + '/dashboard-data')
      .then(onDashboardData)
      .catch(function(e) { console.error('Poll error:', e); });
  }

  startSSE();
  if (!useSSE) poll();  // fallback polling if SSE unavailable
  setInterval(function() {
    if (!useSSE) poll();
  }, POLL_INTERVAL);

  function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function ageAgo(ts) {
    if (!ts) return '';
    var sec = Math.floor((Date.now() / 1000) - ts);
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
    return Math.floor(sec / 3600) + 'h ago';
  }

  function statusBadge(status) {
    var cls = 'status-unknown';
    if (status === 'pending') cls = 'status-pending';
    else if (status === 'in_progress') cls = 'status-in_progress';
    else if (status === 'completed') cls = 'status-completed';
    else if (status === 'blocked') cls = 'status-blocked';
    return '<span class="status-badge ' + cls + '">' + escapeHTML(status) + '</span>';
  }

  // ---- Agent Tree ----
  function renderAgentTree(data) {
    var svg = document.getElementById('agent-tree-svg');
    var agents = data.agents || [];
    var container = document.getElementById('agent-tree-container');

    // Build parent -> children map
    var childrenMap = {};
    var roots = [];
    var agentMap = {};

    // Roll up per-agent lock counts so each node can surface them.
    var lockCounts = {};
    var locks = data.locks || [];
    for (var li = 0; li < locks.length; li++) {
      var owner = locks[li].locked_by;
      lockCounts[owner] = (lockCounts[owner] || 0) + 1;
    }

    for (var i = 0; i < agents.length; i++) {
      var a = agents[i];
      a.lock_count = lockCounts[a.agent_id] || 0;
      agentMap[a.agent_id] = a;
      var parentId = a.parent_id;
      if (!parentId) {
        roots.push(a.agent_id);
      } else {
        if (!childrenMap[parentId]) childrenMap[parentId] = [];
        childrenMap[parentId].push(a.agent_id);
      }
    }

    // BFS to find roots that have children; if no roots with children, show oldest agents
    if (roots.length === 0 && agents.length > 0) {
      roots.push(agents[0].agent_id);
    }

    var NODE_W = 240;
    var NODE_H = 58;
    var H_GAP = 60;
    var V_GAP = 18;
    var PADDING = 24;

    var positions = {};
    var visibleIds = [];

    // Simple tree layout: assign x by level, y by breadth within level
    function layout(id, depth, offset) {
      if (!id || positions[id] !== undefined) return offset;
      var children = childrenMap[id] || [];
      var totalH = children.length > 0 ? children.length * (NODE_H + V_GAP) - V_GAP : NODE_H;
      var startY = offset - totalH / 2;
      var myX = PADDING + depth * (NODE_W + H_GAP);
      var myY = offset;

      // center children around this node
      var childOffsets = [];
      for (var ci = 0; ci < children.length; ci++) {
        var cy = startY + ci * (NODE_H + V_GAP) + NODE_H / 2;
        childOffsets.push(cy);
      }
      for (var ci = 0; ci < children.length; ci++) {
        offset = layout(children[ci], depth + 1, childOffsets[ci]);
      }

      positions[id] = { x: myX, y: myY, children: children };
      visibleIds.push(id);
      return offset;
    }

    var startOffset = 0;
    for (var ri = 0; ri < roots.length; ri++) {
      startOffset = layout(roots[ri], 0, startOffset + (ri > 0 ? NODE_H + V_GAP : 0));
    }

    // Compute bounding box
    var maxX = 0, maxY = 0;
    for (var id in positions) {
      var p = positions[id];
      maxX = Math.max(maxX, p.x + NODE_W);
      maxY = Math.max(maxY, p.y + NODE_H);
    }

    svg.setAttribute('viewBox', '0 0 ' + Math.max(maxX + PADDING, 600) + ' ' + Math.max(maxY + PADDING, 260));

    var rects = ['<rect class="agent-edge" x="0" y="0" width="' + (maxX + PADDING) + '" height="' + (maxY + PADDING) + '" fill="none"/>'];

    // Draw edges first (behind nodes)
    for (var id in positions) {
      var p = positions[id];
      for (var ci = 0; ci < p.children.length; ci++) {
        var child = p.children[ci];
        var cp = positions[child];
        if (!cp) continue;
        var x1 = p.x + NODE_W / 2;
        var y1 = p.y + NODE_H;
        var x2 = cp.x + NODE_W / 2;
        var y2 = cp.y;
        var mx = (x1 + x2) / 2;
        rects.push('<path class="agent-edge" d="M' + x1 + ',' + y1 + ' C' + mx + ',' + y1 + ' ' + mx + ',' + y2 + ' ' + x2 + ',' + y2 + '"/>');
      }
    }

    // Draw nodes
    for (var i = 0; i < visibleIds.length; i++) {
      var id = visibleIds[i];
      var a = agentMap[id];
      var p = positions[id];
      var statusCls = a.status === 'active' ? 'status-active' : (a.status === 'stopped' ? 'status-stopped' : 'status-unknown');
      var taskText = (a.current_task || '');
      var MAX_TASK = 56;
      if (taskText.length > MAX_TASK) taskText = taskText.substring(0, MAX_TASK - 1) + '\u2026';
      var MAX_ID = 30;
      var idText = id.length > MAX_ID ? id.substring(0, MAX_ID - 1) + '\u2026' : id;

      rects.push('<g class="agent-node" transform="translate(' + p.x + ',' + p.y + ')">');
      rects.push('  <rect width="' + NODE_W + '" height="' + NODE_H + '"/>');
      rects.push('  <circle class="status-dot ' + statusCls + '" cx="12" cy="14" r="5"/>');
      rects.push('  <text x="24" y="19" font-weight="600" fill="#e6e8ed">' + escapeHTML(idText) + '</text>');
      if (taskText) {
        rects.push('  <text x="10" y="38" font-size="10.5" fill="#94a3b8">' + escapeHTML(taskText) + '</text>');
      } else {
        rects.push('  <text x="10" y="38" font-size="10.5" font-style="italic" fill="#4b5563">(no current task)</text>');
      }
      if (a.lock_count) {
        rects.push('  <text x="' + (NODE_W - 10) + '" y="51" font-size="9.5" fill="#6b7280" text-anchor="end">' + a.lock_count + ' lock' + (a.lock_count > 1 ? 's' : '') + '</text>');
      }
      rects.push('</g>');
    }

    svg.innerHTML = rects.join('');
  }

  // ---- Task Board ----
  function renderTasks(data) {
    var tbody = document.getElementById('task-tbody');
    var tasks = data.tasks || [];
    if (tasks.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty">No tasks in the registry. Call create_task(parent_agent_id, description) from any agent to populate this board.</td></tr>';
    } else {
      var html = [];
      for (var i = 0; i < tasks.length; i++) {
        var t = tasks[i];
        html.push('<tr>');
        html.push('<td style="font-family:monospace;font-size:11px;color:#8b5cf6;">' + escapeHTML(t.id) + '</td>');
        html.push('<td>' + statusBadge(t.status) + '</td>');
        html.push('<td style="color:#94a3b8;">' + escapeHTML(t.assigned_agent_id || '\u2014') + '</td>');
        html.push('<td style="color:#e6e8ed;">' + escapeHTML(t.description || '') + '</td>');
        html.push('</tr>');
      }
      tbody.innerHTML = html.join('');
    }
    document.getElementById('task-timestamp').textContent = tasks.length + ' tasks \u00b7 ' + new Date().toLocaleTimeString();
  }

  // ---- Work Intents ----
  function renderIntents(data) {
    var grid = document.getElementById('intent-grid');
    var intents = data.work_intents || [];
    if (intents.length === 0) {
      grid.innerHTML = '<div class="empty">No active intents. An agent calls manage_work_intents(action="declare") to reserve a file for a short TTL before starting cooperative work.</div>';
    } else {
      var html = [];
      for (var i = 0; i < intents.length; i++) {
        var it = intents[i];
        html.push('<div class="intent-card">');
        html.push('<div class="intent-agent">' + escapeHTML(it.agent_id) + '</div>');
        html.push('<div class="intent-file">' + escapeHTML(it.document_path) + '</div>');
        html.push('<div class="intent-desc">' + escapeHTML(it.intent) + '</div>');
        html.push('<div class="intent-ttl">TTL: ' + (it.ttl || 60) + 's \u00b7 declared ' + ageAgo(it.declared_at) + '</div>');
        html.push('</div>');
      }
      grid.innerHTML = html.join('');
    }
    document.getElementById('intent-timestamp').textContent = intents.length + ' intent(s) \u00b7 ' + new Date().toLocaleTimeString();
  }

  // ---- Handoffs ----
  function renderHandoffs(data) {
    var list = document.getElementById('handoff-list');
    var handoffs = data.handoffs || [];
    if (handoffs.length === 0) {
      list.innerHTML = '<div class="empty">No handoffs. A handoff is created when one agent is done with a scope and explicitly passes it to one or more named agents (who then acknowledge).</div>';
    } else {
      var html = [];
      for (var i = 0; i < handoffs.length; i++) {
        var h = handoffs[i];
        var toAgents = h.to_agents || (typeof h.to_agents === 'string' ? JSON.parse(h.to_agents) : []);
        var sat = h.satisfied ? 'completed' : (h.status || 'pending');
        var satCls = 'handoff-' + sat;
        html.push('<div class="handoff-item">');
        html.push('<span class="handoff-id">h#' + escapeHTML(String(h.id)) + '</span>');
        html.push('<div class="handoff-route">' + escapeHTML(h.from_agent_id) + ' \u2192 ' + escapeHTML(toAgents.join(', ')) + '</div>');
        if (h.document_path) html.push('<div style="color:#6b7280;font-size:11px;">file: ' + escapeHTML(h.document_path) + '</div>');
        html.push('<span class="handoff-status ' + satCls + '">' + escapeHTML(sat) + '</span>');
        html.push('</div>');
      }
      list.innerHTML = html.join('');
    }
    document.getElementById('handoff-timestamp').textContent = handoffs.length + ' handoff(s) \u00b7 ' + new Date().toLocaleTimeString();
  }

  // ---- Dependencies ----
  function renderDependencies(data) {
    var list = document.getElementById('dep-list');
    var deps = data.dependencies || [];
    if (deps.length === 0) {
      list.innerHTML = '<div class="empty">No blocking relationships. An agent declares one with manage_dependencies(action="declare") to wait on another agent\'s task or lifecycle.</div>';
    } else {
      var html = [];
      for (var i = 0; i < deps.length; i++) {
        var d = deps[i];
        var sat = d.satisfied;
        var satCls = sat ? 'dep-sat-yes' : 'dep-sat-no';
        var satMark = sat ? '\u2713' : '\u2717';
        html.push('<div class="dep-item">');
        html.push('<span class="dep-from">' + escapeHTML(d.dependent_agent_id) + '</span>');
        html.push('<span class="dep-arrow">\u2190</span>');
        html.push('<span class="dep-to">' + escapeHTML(d.depends_on_agent_id) + '</span>');
        if (d.condition) html.push('<span class="dep-condition">' + escapeHTML(d.condition) + '</span>');
        if (d.depends_on_task_id) html.push('<span class="dep-condition">task:' + escapeHTML(d.depends_on_task_id) + '</span>');
        html.push('<span class="dep-sat ' + satCls + '">' + satMark + '</span>');
        html.push('</div>');
      }
      list.innerHTML = html.join('');
    }
    document.getElementById('dep-timestamp').textContent = deps.length + ' dependency/dependencies \u00b7 ' + new Date().toLocaleTimeString();
  }

  // ---- Locks ----
  function renderLocks(data) {
    var list = document.getElementById('lock-list');
    var locks = data.locks || [];
    if (locks.length === 0) {
      list.innerHTML = '<div class="empty">No active locks. Locks appear when an agent calls acquire_lock before writing to a file.</div>';
    } else {
      // Bucket locks by path so the same file groups cleanly.
      var byPath = {};
      var order = [];
      for (var i = 0; i < locks.length; i++) {
        var l = locks[i];
        if (!byPath[l.document_path]) { byPath[l.document_path] = []; order.push(l.document_path); }
        byPath[l.document_path].push(l);
      }
      var html = [];
      for (var pi = 0; pi < order.length; pi++) {
        var path = order[pi];
        var group = byPath[path];
        for (var gi = 0; gi < group.length; gi++) {
          var l = group[gi];
          var lt = l.lock_type || 'exclusive';
          var ltCls = lt === 'exclusive' ? 'lock-exclusive' : 'lock-shared';
          var heldFor = ageAgo(l.locked_at);
          var ttlLeft = Math.max(0, Math.round((l.locked_at + (l.lock_ttl || 300)) - (Date.now() / 1000)));
          var crossing = l.owner_agent_id && l.owner_agent_id !== l.locked_by;
          html.push('<div class="lock-item">');
          html.push('<span class="lock-path">' + escapeHTML(path);
          if (crossing) {
            html.push(' <span style="color:#fbbf24;font-size:11px;" title="this file is owned by ' + escapeHTML(l.owner_agent_id) + '">\u26a0 owned by ' + escapeHTML(l.owner_agent_id) + '</span>');
          }
          html.push('</span>');
          html.push('<div style="display:flex;gap:8px;align-items:center;">');
          html.push('<span style="color:#4b5563;font-size:11px;">held ' + heldFor + ' \u00b7 TTL ' + ttlLeft + 's</span>');
          html.push('<span class="lock-agent">' + escapeHTML(l.locked_by) + '</span>');
          html.push('<span class="lock-type ' + ltCls + '">' + escapeHTML(lt) + '</span>');
          html.push('</div></div>');
        }
      }
      list.innerHTML = html.join('');
    }
    document.getElementById('lock-timestamp').textContent = locks.length + ' lock(s) \u00b7 ' + new Date().toLocaleTimeString();
  }
})();
</script>
</body>
</html>"""


def _serve_dashboard(handler) -> None:
    """Serve the dashboard HTML (used by MCPRequestHandler)."""
    body = DASHBOARD_HTML.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _serve_api_dashboard(handler, engine) -> None:
    """Serve aggregated dashboard data as JSON."""
    import json
    data = get_dashboard_data(engine.connect)
    body = json.dumps(data, default=str).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)