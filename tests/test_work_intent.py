"""Regression tests for the work_intent board (T1.16).

Pre-fix, ``work_intent`` used ``agent_id`` as the sole PK — declaring
intent on file B silently erased intent on file A. ``check_intent_conflict``
also didn't distinguish read/read from write/write, so harmless concurrent
observation got flagged as a conflict.
"""

from __future__ import annotations

import time

import pytest

from coordinationhub import work_intent as _wi


class TestMultipleFilesPerAgent:
    def test_declare_on_second_file_does_not_clobber_first(self, engine, registered_agent):
        engine.declare_work_intent(registered_agent, "a.py", "edit")
        engine.declare_work_intent(registered_agent, "b.py", "edit")

        intents = engine.get_work_intents(registered_agent)["intents"]
        # Paths are normalized relative to engine.project_root; two distinct
        # inputs must still produce two distinct rows.
        assert len(intents) == 2
        paths = {i["document_path"] for i in intents}
        assert len(paths) == 2

    def test_declare_same_file_twice_updates_in_place(self, engine, registered_agent):
        engine.declare_work_intent(registered_agent, "c.py", "edit", ttl=60.0)
        engine.declare_work_intent(registered_agent, "c.py", "review", ttl=120.0)

        intents = engine.get_work_intents(registered_agent)["intents"]
        # Exactly one row for the normalized path.
        assert len(intents) == 1
        assert intents[0]["intent"] == "review"


class TestReadWriteIntentSemantics:
    def _shared_path(self, engine):
        """Return a project-root-relative path that normalize_path preserves."""
        from coordinationhub.paths import normalize_path
        # Run a round-trip through the normalizer so the primitive's
        # storage key matches what our subsequent direct check_intent_conflict
        # calls use as the key.
        return normalize_path("shared.md", engine._storage.project_root)

    def test_two_readers_do_not_conflict(self, engine, two_agents):
        a = two_agents["child"]
        b = two_agents["other"]
        path = self._shared_path(engine)
        engine.declare_work_intent(a, "shared.md", "read")
        engine.declare_work_intent(b, "shared.md", "read")

        conflicts = _wi.check_intent_conflict(
            engine._connect, path,
            exclude_agent_id=b, requesting_intent="read",
        )
        assert conflicts == []

    def test_read_vs_write_is_conflict(self, engine, two_agents):
        a = two_agents["child"]
        b = two_agents["other"]
        path = self._shared_path(engine)
        engine.declare_work_intent(a, "shared.md", "edit")

        conflicts = _wi.check_intent_conflict(
            engine._connect, path,
            exclude_agent_id=b, requesting_intent="read",
        )
        assert len(conflicts) == 1
        assert conflicts[0]["agent_id"] == a

    def test_write_vs_write_is_conflict(self, engine, two_agents):
        a = two_agents["child"]
        b = two_agents["other"]
        path = self._shared_path(engine)
        engine.declare_work_intent(a, "shared.md", "edit")

        conflicts = _wi.check_intent_conflict(
            engine._connect, path,
            exclude_agent_id=b, requesting_intent="edit",
        )
        assert len(conflicts) == 1

    def test_legacy_caller_without_intent_gets_everything(self, engine, two_agents):
        """Callers that don't supply requesting_intent get pre-fix semantics:
        every live intent is returned so the lock layer can warn.
        """
        a = two_agents["child"]
        path = self._shared_path(engine)
        engine.declare_work_intent(a, "shared.md", "read")

        conflicts = _wi.check_intent_conflict(
            engine._connect, path,
            exclude_agent_id=two_agents["other"],
        )
        assert len(conflicts) == 1


class TestClearIntentScoped:
    def test_clear_specific_file_leaves_others(self, engine, registered_agent):
        engine.declare_work_intent(registered_agent, "x.py", "edit")
        engine.declare_work_intent(registered_agent, "y.py", "edit")

        result = engine.clear_work_intent(registered_agent, document_path="x.py")
        assert result["rows_cleared"] == 1

        intents = engine.get_work_intents(registered_agent)["intents"]
        assert len(intents) == 1  # only y.py remains

    def test_clear_all_wipes_every_row(self, engine, registered_agent):
        engine.declare_work_intent(registered_agent, "x.py", "edit")
        engine.declare_work_intent(registered_agent, "y.py", "edit")

        result = engine.clear_work_intent(registered_agent)
        assert result["rows_cleared"] == 2
        assert engine.get_work_intents(registered_agent)["count"] == 0


class TestPruneExpired:
    def test_prune_removes_expired_rows_only(self, engine, two_agents):
        a = two_agents["child"]
        b = two_agents["other"]
        engine.declare_work_intent(a, "stale.py", "edit", ttl=0.01)
        engine.declare_work_intent(b, "fresh.py", "edit", ttl=60.0)
        time.sleep(0.02)

        result = engine.prune_work_intents()
        assert result["pruned"] == 1

        remaining = engine.get_work_intents()["intents"]
        assert len(remaining) == 1
        # The surviving row is b's; identify by agent rather than path.
        assert remaining[0]["agent_id"] == b


class TestPathNormalization:
    def test_declare_normalizes_prefix(self, engine, registered_agent):
        """``./foo.py`` and ``foo.py`` are the same key after normalization."""
        engine.declare_work_intent(registered_agent, "./foo.py", "edit")
        # Second declare with un-prefixed path must update the same row,
        # not add a second one.
        engine.declare_work_intent(registered_agent, "foo.py", "review")

        intents = engine.get_work_intents(registered_agent)["intents"]
        assert len(intents) == 1
        assert intents[0]["intent"] == "review"
