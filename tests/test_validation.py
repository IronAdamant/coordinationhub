"""Regression tests for the stdlib jsonschema validator and its
integration with ``dispatch_tool``.

Covers audit items T6.11 (validator wired at the dispatch boundary),
T6.12 (payload constraints + size enforced at primitive), T7.44
(``default: None`` with non-null types treated as "absent"), T7.46
(mode-gated required fields via ``oneOf``), T7.49 (mode/action enums).
"""

from __future__ import annotations

import pytest

from coordinationhub.validation import (
    ValidationError,
    validate,
    validate_tool_arguments,
)
from coordinationhub.dispatch import dispatch_tool


# ----------------------------------------------------------------------
# Core validator behaviour
# ----------------------------------------------------------------------


class TestTypeChecks:
    def test_string_accepts_str(self):
        validate("hi", {"type": "string"})

    def test_string_rejects_int(self):
        with pytest.raises(ValidationError, match="type 'string'"):
            validate(42, {"type": "string"})

    def test_integer_rejects_bool(self):
        # Python conflates bool and int; schema integer must reject bool.
        with pytest.raises(ValidationError):
            validate(True, {"type": "integer"})

    def test_integer_rejects_float(self):
        with pytest.raises(ValidationError):
            validate(1.5, {"type": "integer"})

    def test_number_accepts_int_and_float(self):
        validate(1, {"type": "number"})
        validate(1.5, {"type": "number"})

    def test_number_rejects_bool(self):
        with pytest.raises(ValidationError):
            validate(True, {"type": "number"})

    def test_null_type(self):
        validate(None, {"type": "null"})
        with pytest.raises(ValidationError):
            validate("nope", {"type": "null"})

    def test_type_list_any_matches(self):
        validate("s", {"type": ["string", "integer"]})
        validate(3, {"type": ["string", "integer"]})
        with pytest.raises(ValidationError):
            validate(3.5, {"type": ["string", "integer"]})

    def test_unknown_type_passes_through(self):
        # Forwards compat: if we don't know the type name, don't reject.
        validate("x", {"type": "bytes"})


class TestEnum:
    def test_enum_match(self):
        validate("a", {"enum": ["a", "b"]})

    def test_enum_miss(self):
        with pytest.raises(ValidationError, match="must be one of"):
            validate("c", {"enum": ["a", "b"]})


class TestBounds:
    def test_minimum(self):
        with pytest.raises(ValidationError, match=">= 0"):
            validate(-1, {"type": "integer", "minimum": 0})
        validate(0, {"type": "integer", "minimum": 0})

    def test_maximum(self):
        with pytest.raises(ValidationError, match="<= 10"):
            validate(11, {"type": "integer", "maximum": 10})

    def test_min_length(self):
        with pytest.raises(ValidationError, match="length >= 1"):
            validate("", {"type": "string", "minLength": 1})

    def test_max_length(self):
        with pytest.raises(ValidationError, match="length <= 5"):
            validate("toolong", {"type": "string", "maxLength": 5})


class TestObject:
    def test_required_missing(self):
        with pytest.raises(ValidationError, match="missing required property 'agent_id'"):
            validate({}, {
                "type": "object",
                "required": ["agent_id"],
                "properties": {"agent_id": {"type": "string"}},
            })

    def test_additional_properties_false_rejects_extras(self):
        with pytest.raises(ValidationError, match="unexpected propert"):
            validate({"agent_id": "x", "extra": 1}, {
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "additionalProperties": False,
            })

    def test_additional_properties_default_allows_extras(self):
        # Default is True — pre-T6.11 behaviour.
        validate({"agent_id": "x", "extra": 1}, {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
        })

    def test_optional_none_treated_as_absent(self):
        """T7.44: a None for an optional string field should NOT error."""
        schema = {
            "type": "object",
            "properties": {"parent_id": {"type": "string"}},
        }
        validate({"parent_id": None}, schema)

    def test_required_none_still_errors(self):
        """A required string field that's explicitly None still fails."""
        schema = {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
            "required": ["agent_id"],
        }
        with pytest.raises(ValidationError):
            validate({"agent_id": None}, schema)

    def test_type_list_with_null_accepts_none(self):
        schema = {
            "type": "object",
            "properties": {"maybe": {"type": ["string", "null"]}},
        }
        validate({"maybe": None}, schema)
        validate({"maybe": "x"}, schema)


class TestArray:
    def test_items_schema_applied(self):
        with pytest.raises(ValidationError, match=r"\[1\] must be of type 'string'"):
            validate(["a", 1], {
                "type": "array",
                "items": {"type": "string"},
            })


class TestOneOf:
    def test_one_branch_matches(self):
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
            ],
        }
        validate("x", schema)
        validate(3, schema)

    def test_no_branch_matches(self):
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
            ],
        }
        with pytest.raises(ValidationError):
            validate(3.5, schema)


# ----------------------------------------------------------------------
# Dispatch integration
# ----------------------------------------------------------------------


class TestDispatchValidation:
    def test_valid_call_dispatches(self, engine):
        result = dispatch_tool(engine, "register_agent", {
            "agent_id": "hub.validator.test.1",
        })
        assert result.get("agent_id") == "hub.validator.test.1"

    def test_missing_required_rejected(self, engine):
        with pytest.raises(ValidationError, match="missing required property"):
            dispatch_tool(engine, "register_agent", {})

    def test_wrong_type_rejected(self, engine):
        with pytest.raises(ValidationError, match="type 'string'"):
            dispatch_tool(engine, "register_agent", {"agent_id": 123})

    def test_null_for_optional_accepted(self, engine):
        """T7.44: explicit None for optional fields must still dispatch."""
        result = dispatch_tool(engine, "register_agent", {
            "agent_id": "hub.validator.test.nullopt",
            "parent_id": None,
            "graph_agent_id": None,
        })
        assert result.get("agent_id") == "hub.validator.test.nullopt"

    def test_unknown_enum_rejected(self, engine, registered_agent):
        # list_agents.active_only is boolean — string triggers the type
        # rejection. For an enum-specific case, manage_work_intents
        # action must be one of declare|get|clear.
        with pytest.raises(ValidationError, match="must be one of"):
            dispatch_tool(engine, "manage_work_intents", {
                "action": "not_a_real_action",
                "agent_id": registered_agent,
            })

    def test_negative_stale_timeout_rejected(self, engine):
        with pytest.raises(ValidationError, match=">= 0"):
            dispatch_tool(engine, "list_agents", {"stale_timeout": -1})


class TestModeGatedRequired:
    """T7.46: manage_work_intents action='declare' requires document_path
    and intent; action='get' / 'clear' don't.
    """

    def test_declare_requires_document_path(self, engine):
        with pytest.raises(ValidationError):
            dispatch_tool(engine, "manage_work_intents", {
                "action": "declare",
                "agent_id": "hub.v.1",
                # missing document_path + intent
            })

    def test_declare_with_all_fields_validates(self, engine, registered_agent):
        # Should pass validation (engine call may still do its own thing).
        result = dispatch_tool(engine, "manage_work_intents", {
            "action": "declare",
            "agent_id": registered_agent,
            "document_path": "/tmp/x.py",
            "intent": "writing",
        })
        assert isinstance(result, dict)

    def test_get_does_not_require_document_path(self, engine, registered_agent):
        # No document_path or intent supplied — must pass.
        result = dispatch_tool(engine, "manage_work_intents", {
            "action": "get",
            "agent_id": registered_agent,
        })
        assert isinstance(result, dict)


class TestMessagingModeGating:
    """T7.46 + T7.49: send requires from/to/message_type; get / mark_read
    don't."""

    def test_send_requires_message_type(self, engine, registered_agent):
        with pytest.raises(ValidationError):
            dispatch_tool(engine, "manage_messages", {
                "action": "send",
                "agent_id": registered_agent,
                "from_agent_id": registered_agent,
                "to_agent_id": registered_agent,
                # missing message_type
            })

    def test_get_does_not_require_send_fields(self, engine, registered_agent):
        result = dispatch_tool(engine, "manage_messages", {
            "action": "get",
            "agent_id": registered_agent,
        })
        assert isinstance(result, dict)

    def test_unknown_action_rejected(self, engine, registered_agent):
        with pytest.raises(ValidationError, match="must be one of"):
            dispatch_tool(engine, "manage_messages", {
                "action": "bogus",
                "agent_id": registered_agent,
            })


class TestMaxLengthEnforcement:
    """T6.11 + T6.14: string caps declared in the schema block oversized
    input before primitives have to truncate."""

    def test_create_task_rejects_over_description_cap(self, engine, registered_agent):
        from coordinationhub.limits import MAX_DESCRIPTION
        oversize = "x" * (MAX_DESCRIPTION + 1)
        with pytest.raises(ValidationError, match="length <="):
            dispatch_tool(engine, "create_task", {
                "task_id": "task.1",
                "parent_agent_id": registered_agent,
                "description": oversize,
            })


class TestValidateToolArgumentsHelper:
    def test_prefixes_tool_name_on_error(self):
        schema = {
            "type": "object",
            "required": ["x"],
            "properties": {"x": {"type": "string"}},
        }
        with pytest.raises(ValidationError, match="^my_tool:"):
            validate_tool_arguments("my_tool", {}, schema)
