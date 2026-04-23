"""Plugin registry for CoordinationHub.

Discovers and registers optional plugin modules. Each plugin exposes:
  - register_tools(dispatch_table: dict) -> None
  - register_cli(subparsers) -> None

T2.5: plugin loading is restricted to an explicit allow-list
(``ALLOWED_PLUGINS``). Arbitrary names — especially those smuggled in
through ``plugin_names=...`` or via ``sys.path`` manipulation — are
rejected before ``__import__`` so untrusted modules cannot run on hub
start-up.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

_Plugin = dict[str, Any]


# T2.5: the only plugin names hub start-up will attempt to import. Any
# caller supplying a name not in this set gets silently skipped.
ALLOWED_PLUGINS: frozenset[str] = frozenset({
    "assessment",
    "graph",
    "dashboard",
})


def _load_plugin(name: str) -> _Plugin | None:
    if name not in ALLOWED_PLUGINS:
        _log.warning(
            "plugin load rejected: %r not in ALLOWED_PLUGINS", name,
        )
        return None
    try:
        mod = __import__(f"coordinationhub.plugins.{name}", fromlist=["register_tools", "register_cli"])
        plugin: _Plugin = {"name": name, "module": mod}
        if hasattr(mod, "register_tools"):
            plugin["register_tools"] = mod.register_tools
        if hasattr(mod, "register_cli"):
            plugin["register_cli"] = mod.register_cli
        return plugin
    except Exception as exc:
        _log.warning("plugin %r failed to load: %s", name, exc)
        return None


class PluginRegistry:
    """Collects and registers CoordinationHub plugins."""

    DEFAULT_PLUGINS = ("assessment", "graph", "dashboard")

    def __init__(self, plugin_names: tuple[str, ...] | None = None) -> None:
        self._plugins: list[_Plugin] = []
        names = plugin_names if plugin_names is not None else self.DEFAULT_PLUGINS
        for name in names:
            plugin = _load_plugin(name)
            if plugin:
                self._plugins.append(plugin)

    def register_tools(self, dispatch_table: dict[str, tuple[str, list[str]]]) -> None:
        for plugin in self._plugins:
            fn = plugin.get("register_tools")
            if fn:
                fn(dispatch_table)

    def register_cli(self, subparsers) -> None:
        for plugin in self._plugins:
            fn = plugin.get("register_cli")
            if fn:
                fn(subparsers)

    def list_plugins(self) -> list[str]:
        return [p["name"] for p in self._plugins]
