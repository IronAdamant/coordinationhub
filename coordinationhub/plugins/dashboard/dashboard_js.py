"""Client-side JavaScript for the CoordinationHub dashboard.

Extracted from :mod:`dashboard_html` so each piece stays well under the
project's 500-LOC module budget. The string is injected between
``<script>...</script>`` at template-render time.
"""

from __future__ import annotations


DASHBOARD_JS = r"""
(function() {
  var POLL_INTERVAL = 5000;

  // Agent-tree pan + zoom state — initialised here so any function that
  // runs early (e.g. the very first SSE-delivered render) sees a live
  // object rather than ``undefined`` from a hoisted-but-unassigned ``var``.
  var treeState = { zoom: null, panX: 0, panY: 0 };
  var treeContentBox = { w: 0, h: 0, viewW: 0, viewH: 0 };
  var ZOOM_MIN = 0.2, ZOOM_MAX = 4.0;
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

    var NODE_W = 320;
    var NODE_H = 70;
    var H_GAP = 90;
    var V_GAP = 24;
    var PADDING = 28;

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

    // Set viewBox to the SVG's actual pixel size so 1 viewBox unit == 1 CSS pixel.
    // The inner ``<g class="tree-root">`` group is what scales/translates for
    // pan + zoom; the viewBox itself stays put as a stable viewport.
    var viewW = Math.max(svg.clientWidth || 800, 200);
    var viewH = Math.max(svg.clientHeight || 320, 200);
    svg.setAttribute('viewBox', '0 0 ' + viewW + ' ' + viewH);

    // Track the rendered tree's content bounding box so the "fit" button can
    // recompute zoom + pan to fit everything in view.
    treeContentBox.w = maxX + PADDING;
    treeContentBox.h = maxY + PADDING;
    treeContentBox.viewW = viewW;
    treeContentBox.viewH = viewH;

    // First render — initialize zoom/pan to fit-to-view.
    if (treeState.zoom === null) {
      fitTreeToView(false);
    }

    var rects = [
      '<g class="tree-root" transform="translate(' + treeState.panX + ',' + treeState.panY + ') scale(' + treeState.zoom + ')">'
    ];

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
      var MAX_TASK = 76;
      if (taskText.length > MAX_TASK) taskText = taskText.substring(0, MAX_TASK - 1) + '\u2026';
      var MAX_ID = 38;
      var idText = id.length > MAX_ID ? id.substring(0, MAX_ID - 1) + '\u2026' : id;

      rects.push('<g class="agent-node" transform="translate(' + p.x + ',' + p.y + ')">');
      rects.push('  <rect width="' + NODE_W + '" height="' + NODE_H + '"/>');
      rects.push('  <circle class="status-dot ' + statusCls + '" cx="14" cy="16" r="5"/>');
      rects.push('  <text x="28" y="21" font-weight="600" fill="#e6e8ed">' + escapeHTML(idText) + '</text>');
      if (taskText) {
        rects.push('  <text x="12" y="44" font-size="10.5" fill="#94a3b8">' + escapeHTML(taskText) + '</text>');
      } else {
        rects.push('  <text x="12" y="44" font-size="10.5" font-style="italic" fill="#4b5563">(no current task)</text>');
      }
      if (a.lock_count) {
        rects.push('  <text x="' + (NODE_W - 12) + '" y="61" font-size="9.5" fill="#6b7280" text-anchor="end">' + a.lock_count + ' lock' + (a.lock_count > 1 ? 's' : '') + '</text>');
      }
      rects.push('</g>');
    }
    rects.push('</g>');

    svg.innerHTML = rects.join('');
    updateZoomLabel();
  }

  // ---- Agent tree pan + zoom ----
  // (treeState / treeContentBox / ZOOM_MIN / ZOOM_MAX are declared at the
  // top of this IIFE so render functions called from the first SSE event
  // see a live object instead of ``undefined``.)

  function applyTreeTransform() {
    var svg = document.getElementById('agent-tree-svg');
    if (!svg) return;
    var g = svg.querySelector('.tree-root');
    if (!g) return;
    g.setAttribute('transform',
      'translate(' + treeState.panX + ',' + treeState.panY + ') scale(' + treeState.zoom + ')');
    updateZoomLabel();
  }

  function updateZoomLabel() {
    var lbl = document.getElementById('tree-zoom-level');
    if (lbl && treeState.zoom !== null) {
      lbl.textContent = Math.round(treeState.zoom * 100) + '%';
    }
  }

  function setZoom(newZoom, anchorX, anchorY) {
    newZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, newZoom));
    if (treeState.zoom === null) treeState.zoom = 1;
    if (anchorX === undefined) {
      var svg = document.getElementById('agent-tree-svg');
      anchorX = svg.clientWidth / 2;
      anchorY = svg.clientHeight / 2;
    }
    // Keep anchor point under cursor stable while zoom changes
    treeState.panX = anchorX - (anchorX - treeState.panX) * (newZoom / treeState.zoom);
    treeState.panY = anchorY - (anchorY - treeState.panY) * (newZoom / treeState.zoom);
    treeState.zoom = newZoom;
    applyTreeTransform();
  }

  function fitTreeToView(applyNow) {
    if (!treeContentBox.w || !treeContentBox.h) return;
    var marginPct = 0.05;
    var fitZoom = Math.min(
      (treeContentBox.viewW * (1 - marginPct * 2)) / treeContentBox.w,
      (treeContentBox.viewH * (1 - marginPct * 2)) / treeContentBox.h,
      1.0
    );
    treeState.zoom = Math.max(ZOOM_MIN, fitZoom);
    treeState.panX = (treeContentBox.viewW - treeContentBox.w * treeState.zoom) / 2;
    treeState.panY = (treeContentBox.viewH - treeContentBox.h * treeState.zoom) / 2;
    if (applyNow !== false) applyTreeTransform();
  }

  function bindTreeControls() {
    var container = document.getElementById('agent-tree-container');
    var svg = document.getElementById('agent-tree-svg');
    if (!container || !svg) return;

    // Wheel zoom — anchored at cursor position
    svg.addEventListener('wheel', function(e) {
      e.preventDefault();
      var rect = svg.getBoundingClientRect();
      var ax = e.clientX - rect.left;
      var ay = e.clientY - rect.top;
      var factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      setZoom((treeState.zoom || 1) * factor, ax, ay);
    }, { passive: false });

    // Click + drag to pan
    var dragOrigin = null;
    svg.addEventListener('mousedown', function(e) {
      if (e.button !== 0) return;
      dragOrigin = { x: e.clientX - treeState.panX, y: e.clientY - treeState.panY };
      container.classList.add('panning');
    });
    window.addEventListener('mousemove', function(e) {
      if (!dragOrigin) return;
      treeState.panX = e.clientX - dragOrigin.x;
      treeState.panY = e.clientY - dragOrigin.y;
      applyTreeTransform();
    });
    window.addEventListener('mouseup', function() {
      dragOrigin = null;
      container.classList.remove('panning');
    });

    // Buttons
    document.getElementById('tree-zoom-in').addEventListener('click', function() {
      setZoom((treeState.zoom || 1) * 1.25);
    });
    document.getElementById('tree-zoom-out').addEventListener('click', function() {
      setZoom((treeState.zoom || 1) / 1.25);
    });
    document.getElementById('tree-fit').addEventListener('click', function() {
      fitTreeToView();
    });
  }

  bindTreeControls();

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
          html.push('<span class="lock-path">' + escapeHTML(path));
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
"""
