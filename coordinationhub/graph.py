"""In-memory CoordinationGraph representation.

Provides lookup helpers for agents, handoffs, and graph metadata.
Uses graph_validate for validation. Zero internal dependencies on other coordinationhub modules.
"""

from __future__ import annotations

from typing import Any

from .graph_validate import validate_graph


class CoordinationGraph:
    """In-memory coordination graph with lookup helpers."""

    __slots__ = ("_data", "_agents", "_handoff_map")

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._agents: dict[str, dict[str, Any]] = {
            a["id"]: a for a in data.get("agents", []) if "id" in a
        }
        self._handoff_map: dict[str, list[dict[str, Any]]] = {}
        for h in data.get("handoffs", []):
            key = h.get("from", "")
            if key:
                self._handoff_map.setdefault(key, []).append(h)

    @property
    def raw(self) -> dict[str, Any]:
        return self._data

    @property
    def agents(self) -> dict[str, dict[str, Any]]:
        return self._agents

    @property
    def handoffs(self) -> list[dict[str, Any]]:
        return self._data.get("handoffs", [])

    @property
    def escalation(self) -> dict[str, Any] | None:
        return self._data.get("escalation")

    @property
    def assessment(self) -> dict[str, Any] | None:
        return self._data.get("assessment")

    def agent(self, graph_id: str) -> dict[str, Any] | None:
        return self._agents.get(graph_id)

    def outgoing_handoffs(self, from_id: str) -> list[dict[str, Any]]:
        return self._handoff_map.get(from_id, [])

    def handoff_targets(self, from_id: str) -> list[str]:
        return [h["to"] for h in self.outgoing_handoffs(from_id)]

    def is_valid(self) -> bool:
        return validate_graph(self._data)["valid"]

    def validation_errors(self) -> list[str]:
        return validate_graph(self._data)["errors"]

    def agent_ids(self) -> list[str]:
        return list(self._agents.keys())
