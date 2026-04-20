"""Tests for the plugin registry."""

from __future__ import annotations

import pytest

from coordinationhub.plugins.registry import PluginRegistry, _load_plugin


class TestLoadPlugin:
    def test_load_existing_plugin(self):
        plugin = _load_plugin("dashboard")
        assert plugin is not None
        assert plugin["name"] == "dashboard"
        assert "register_tools" in plugin or "register_cli" in plugin

    def test_load_missing_plugin_returns_none(self):
        assert _load_plugin("no_such_plugin") is None


class TestPluginRegistry:
    def test_default_plugins_load(self):
        registry = PluginRegistry()
        names = registry.list_plugins()
        assert "dashboard" in names
        assert "graph" in names
        assert "assessment" in names

    def test_custom_plugin_list(self):
        registry = PluginRegistry(plugin_names=("dashboard",))
        assert registry.list_plugins() == ["dashboard"]

    def test_register_tools_no_op_without_plugins(self):
        registry = PluginRegistry(plugin_names=())
        dispatch = {}
        registry.register_tools(dispatch)
        assert dispatch == {}

    def test_register_cli_no_op_without_plugins(self):
        registry = PluginRegistry(plugin_names=())
        registry.register_cli(None)
        assert registry.list_plugins() == []
