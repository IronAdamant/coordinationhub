"""Minimal stdlib jsonschema validator for MCP tool arguments.

T6.11: ``TOOL_SCHEMAS`` declared parameter shapes but nothing enforced
them. Tools received whatever the client sent and relied on engine
methods raising TypeError or downstream primitives coercing the value.
This module wires the schemas into ``dispatch_tool`` so malformed calls
are rejected with a specific, actionable error before any DB work
happens.

Scope: only the JSON Schema draft-7 keywords the repo actually uses. No
third-party dependencies (the repo is zero-dep by policy), no ``$ref``,
no ``oneOf``/``anyOf``/``allOf`` except for the narrow ``oneOf`` pattern
used by mode-gated dispatchers (T7.46). Extending this is intentional —
we don't want to ship a whole jsonschema library's complexity surface
when a small focused subset is enough.

Supported keywords:

* ``type``: string | integer | number | boolean | object | array | null
  (a list of strings means "any of these types")
* ``required``: list of property names that must be present
* ``properties``: dict mapping property name → sub-schema
* ``additionalProperties``: bool (default True)
* ``enum``: list of valid values
* ``minimum`` / ``maximum``: numeric bounds (inclusive)
* ``minLength`` / ``maxLength``: string length bounds (Unicode code points)
* ``items``: schema applied to every array element
* ``oneOf``: list of schemas; exactly one must validate
* ``default``: passed through (documentation only; not enforced)

Everything else is ignored so adding a ``description`` or ``format`` hint
doesn't blow up validation.
"""

from __future__ import annotations

from typing import Any


class ValidationError(ValueError):
    """Raised when tool arguments do not match the declared schema.

    Inherits from ``ValueError`` so existing dispatch callers that catch
    ValueError (the previous "unknown tool" error path) keep working.
    """


_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, bool) is False and isinstance(v, int),
    "number": lambda v: isinstance(v, bool) is False and isinstance(v, (int, float)),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


def _check_type(value: Any, type_spec: Any) -> bool:
    """Return True iff ``value`` matches ``type_spec``."""
    if isinstance(type_spec, list):
        return any(_check_type(value, t) for t in type_spec)
    checker = _TYPE_CHECKS.get(type_spec)
    if checker is None:
        # Unknown type names pass through — don't reject a schema shape
        # just because we haven't taught the validator about it.
        return True
    return checker(value)


def validate(value: Any, schema: dict[str, Any], path: str = "") -> None:
    """Raise ``ValidationError`` if ``value`` does not conform to ``schema``.

    ``path`` is the dotted property path for error messages (empty for
    the top-level call).
    """
    if not isinstance(schema, dict):
        return

    # oneOf — exactly one branch must validate. Used for mode-gated
    # dispatchers (e.g. manage_work_intents declare vs get vs clear).
    one_of = schema.get("oneOf")
    if one_of:
        matches = 0
        last_error: ValidationError | None = None
        for sub in one_of:
            try:
                validate(value, sub, path)
                matches += 1
            except ValidationError as exc:
                last_error = exc
        if matches != 1:
            if last_error is not None and matches == 0:
                raise last_error
            raise ValidationError(
                f"{path or 'value'} matched {matches} oneOf branches "
                f"(expected exactly 1)",
            )

    # type
    type_spec = schema.get("type")
    if type_spec is not None and not _check_type(value, type_spec):
        raise ValidationError(
            f"{path or 'value'} must be of type {type_spec!r} "
            f"(got {type(value).__name__})",
        )

    # enum
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        raise ValidationError(
            f"{path or 'value'} must be one of {enum!r} (got {value!r})",
        )

    # object: properties, required, additionalProperties
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise ValidationError(
                    f"{path or 'value'}: missing required property {key!r}",
                )
        # additionalProperties defaults to True for backward compat.
        # When False, any key not in properties rejects.
        allow_additional = schema.get("additionalProperties", True)
        if allow_additional is False:
            extras = set(value) - set(properties)
            if extras:
                raise ValidationError(
                    f"{path or 'value'}: unexpected propert"
                    f"{'y' if len(extras) == 1 else 'ies'} "
                    f"{sorted(extras)!r}",
                )
        for key, sub_value in value.items():
            sub_schema = properties.get(key)
            if sub_schema is not None:
                # T7.44: accept explicit ``None`` as "field absent" for
                # optional properties whose type doesn't allow null.
                # Many existing callers send ``null`` to mean "unset";
                # T3.5 explicitly preserves that through dispatch.
                # Rejecting null here would break every such client.
                # Required properties still reject null (the required
                # check above fires only on *missing* keys; the
                # type-check below will catch a null for a required
                # non-null field).
                if sub_value is None and key not in required:
                    type_spec = sub_schema.get("type")
                    allows_null = (
                        type_spec == "null"
                        or (isinstance(type_spec, list) and "null" in type_spec)
                        or type_spec is None
                    )
                    if not allows_null:
                        # Treat as absent — skip sub-validation.
                        continue
                sub_path = f"{path}.{key}" if path else key
                validate(sub_value, sub_schema, sub_path)

    # array: items
    if isinstance(value, list):
        items_schema = schema.get("items")
        if items_schema is not None:
            for i, item in enumerate(value):
                validate(item, items_schema, f"{path}[{i}]")

    # numeric bounds
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            raise ValidationError(
                f"{path or 'value'} must be >= {minimum} (got {value})",
            )
        maximum = schema.get("maximum")
        if maximum is not None and value > maximum:
            raise ValidationError(
                f"{path or 'value'} must be <= {maximum} (got {value})",
            )

    # string bounds
    if isinstance(value, str):
        min_length = schema.get("minLength")
        if min_length is not None and len(value) < min_length:
            raise ValidationError(
                f"{path or 'value'} must have length >= {min_length} "
                f"(got {len(value)})",
            )
        max_length = schema.get("maxLength")
        if max_length is not None and len(value) > max_length:
            raise ValidationError(
                f"{path or 'value'} must have length <= {max_length} "
                f"(got {len(value)})",
            )


def validate_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    parameters_schema: dict[str, Any],
) -> None:
    """Validate ``arguments`` against a tool's ``parameters`` schema.

    Raises ``ValidationError`` with the offending tool name prefixed so
    log messages are self-describing.
    """
    try:
        validate(arguments, parameters_schema)
    except ValidationError as exc:
        raise ValidationError(f"{tool_name}: {exc}") from None
