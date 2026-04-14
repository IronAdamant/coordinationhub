"""Plugin registry for CoordinationHub.

Discovers and registers optional plugin modules. Each plugin exposes:
  - register_tools(dispatch_table: dict) -> None
  - register_cli(subparsers) -> None
"""

from __future__ import annotations

from typing import Any, Callable

_Plugin = dict[str, Any]


def _load_plugin(name: str) -> _Plugin | None:
    try:
        mod = __import__(f"coordinationhub.plugins.{name}", fromlist=["register_tools", "register_cli"])
        plugin: _Plugin = {"name": name, "module": mod}
        if hasattr(mod, "register_tools"):
            plugin["register_tools"] = mod.register_tools
        if hasattr(mod, "register_cli"):
            plugin["register_cli"] = mod.register_cli
        return plugin
    except Exception:
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
