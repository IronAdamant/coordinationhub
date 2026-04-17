"""Self-contained HTML for the CoordinationHub dashboard.

The page template lives here; CSS lives in :mod:`dashboard_css` and
JavaScript lives in :mod:`dashboard_js`. Splitting the three keeps
every file well under the project's 500-LOC module budget. The
final ``DASHBOARD_HTML`` constant below is assembled at import time.
"""

from __future__ import annotations

from .dashboard_css import DASHBOARD_CSS
from .dashboard_js import DASHBOARD_JS


_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CoordinationHub — Agent Swarm Dashboard</title>
<style>"""

_BODY = """</style>
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
    <div class="panel-blurb">Each box is one agent. Parent&rarr;child edges show spawn lineage. The small grey line under the agent ID is the agent's current task. Drag to pan, mouse-wheel to zoom, or use the controls.</div>
    <div class="agent-tree-container" id="agent-tree-container">
      <div class="tree-controls">
        <span class="zoom-level" id="tree-zoom-level">100%</span>
        <button id="tree-zoom-out" title="Zoom out" type="button">&minus;</button>
        <button id="tree-fit" title="Fit tree to view" type="button">&#9974;</button>
        <button id="tree-zoom-in" title="Zoom in" type="button">+</button>
      </div>
      <svg id="agent-tree-svg" preserveAspectRatio="xMidYMid meet"></svg>
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

<script>"""

_TAIL = """</script>
</body>
</html>"""


DASHBOARD_HTML = _HEAD + DASHBOARD_CSS + _BODY + DASHBOARD_JS + _TAIL
