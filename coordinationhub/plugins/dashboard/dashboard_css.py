"""CSS for the CoordinationHub dashboard.

Extracted from :mod:`dashboard_html` so each piece stays well under the
project's 500-LOC module budget. The string is injected between
``<style>...</style>`` at template-render time.
"""

from __future__ import annotations


DASHBOARD_CSS = r"""
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
  .agent-tree-container { position: relative; height: 340px; overflow: hidden; cursor: grab; user-select: none; }
  .agent-tree-container.panning { cursor: grabbing; }
  .agent-tree-container svg { display: block; width: 100%; height: 100%; }
  .tree-controls { position: absolute; top: 10px; right: 10px; display: flex; gap: 4px; z-index: 2; }
  .tree-controls button {
    background: #1e212d; border: 1px solid #2d3148; color: #94a3b8;
    width: 28px; height: 28px; border-radius: 4px; cursor: pointer;
    font-size: 14px; font-weight: 600; line-height: 1; padding: 0;
    display: flex; align-items: center; justify-content: center;
  }
  .tree-controls button:hover { background: #2a2d3a; color: #e6e8ed; border-color: #4b5563; }
  .tree-controls .zoom-level { font-size: 11px; color: #6b7280; margin-right: 6px; align-self: center; min-width: 32px; text-align: right; }

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
"""
