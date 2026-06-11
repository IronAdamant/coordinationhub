# coordinationhub/validation.py

Minimal stdlib JSON-Schema (draft-7 subset) validator for MCP tool arguments (T6.11) — rejects malformed calls with a specific, actionable error before any DB work happens.

## Key Functions / Classes
- `ValidationError` — subclass of `ValueError` so existing dispatch callers catching ValueError (the "unknown tool" path) keep working.
- `validate(value, schema, path="")` — recursive validator; `path` is the dotted property path used in error messages.
- `validate_tool_arguments(tool_name, arguments, parameters_schema)` — wraps `validate`, prefixing the tool name onto any error.
- `_TYPE_CHECKS` / `_check_type` — type predicates; note `integer`/`number` explicitly exclude `bool` (a Python bool is an int).

## Design Notes
- Intentionally a narrow subset, not a jsonschema library: supports `type` (incl. type lists), `required`, `properties`, `additionalProperties` (bool, default True), `enum`, `minimum`/`maximum`, `minLength`/`maxLength`, `items`, and `oneOf` (only for mode-gated dispatchers, T7.46). No `$ref`, no `anyOf`/`allOf`. The repo is zero-dep by policy.
- Unknown keywords and unknown type names are ignored/pass — adding a `description` or `format` hint must not blow up validation.
- T7.44: explicit `None` on an *optional* property whose type doesn't allow null is treated as "field absent" and skipped — many clients send `null` to mean unset, and T3.5 deliberately preserves `None` through dispatch. Required properties still reject null via the type check (the `required` check only fires on missing keys).
- `oneOf` requires exactly one branch to match; on zero matches the last branch error is re-raised for a more specific message.
- String bounds are measured in Unicode code points, matching `limits.py` caps.

## Relationships
Imports: none from the package.
Imported by: `dispatch.py` (lazily, inside `dispatch_tool`).
