//! Schema check. Error text matches the Python validator.

use crate::json::{self, Value};

pub struct Schema {
    pub tools_version: String,
    pub tools: Vec<(String, Value)>,
    pub allowed: Vec<(String, Vec<String>)>,
}

pub fn load_schema() -> Schema {
    let raw = include_str!("../assets/tools.json");
    let value = json::parse(raw).expect("embedded tool schema is valid JSON");
    let tools_version = value
        .get("tools_version")
        .and_then(Value::as_str)
        .unwrap_or("1.0.0")
        .to_string();
    let mut tools = Vec::new();
    if let Some(Value::Object(pairs)) = value.get("tools") {
        for (name, schema) in pairs {
            tools.push((name.clone(), schema.clone()));
        }
    }
    let mut allowed = Vec::new();
    if let Some(Value::Object(pairs)) = value.get("dispatch") {
        for (name, spec) in pairs {
            let args = spec
                .get("args")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(|s| s.to_string())
                        .collect()
                })
                .unwrap_or_default();
            allowed.push((name.clone(), args));
        }
    }
    Schema {
        tools_version,
        tools,
        allowed,
    }
}

pub fn validate_tool(schema: &Schema, tool: &str, args: &Value) -> Result<(), String> {
    let Some((_, spec)) = schema.tools.iter().find(|(name, _)| name == tool) else {
        return Ok(());
    };
    let Some(params) = spec.get("parameters") else {
        return Ok(());
    };
    validate(args, params, "").map_err(|err| format!("{tool}: {err}"))
}

pub fn validate(value: &Value, schema: &Value, path: &str) -> Result<(), String> {
    if let Some(Value::Array(branches)) = schema.get("oneOf") {
        let mut matches = 0;
        let mut last = None;
        for branch in branches {
            match validate(value, branch, path) {
                Ok(()) => matches += 1,
                Err(err) => last = Some(err),
            }
        }
        if matches != 1 {
            if matches == 0 {
                if let Some(err) = last {
                    return Err(err);
                }
            }
            let where_ = if path.is_empty() { "value" } else { path };
            return Err(format!(
                "{where_} matched {matches} oneOf branches (expected exactly 1)"
            ));
        }
    }
    if let Some(type_spec) = schema.get("type") {
        if !type_ok(value, type_spec) {
            let where_ = if path.is_empty() { "value" } else { path };
            return Err(format!(
                "{where_} must be of type {} (got {})",
                json::py_repr(type_spec),
                value.py_type_name()
            ));
        }
    }
    if let Some(Value::Array(enum_vals)) = schema.get("enum") {
        if !enum_vals.iter().any(|item| item == value) {
            let where_ = if path.is_empty() { "value" } else { path };
            return Err(format!(
                "{where_} must be one of {} (got {})",
                json::py_repr(&Value::Array(enum_vals.clone())),
                json::py_repr(value)
            ));
        }
    }
    if let Value::Object(pairs) = value {
        let properties = schema
            .get("properties")
            .and_then(Value::as_object)
            .unwrap_or(&[]);
        let required: Vec<&str> = schema
            .get("required")
            .and_then(Value::as_array)
            .map(|items| items.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        for key in &required {
            if !pairs.iter().any(|(name, _)| name == key) {
                let where_ = if path.is_empty() { "value" } else { path };
                return Err(format!(
                    "{where_}: missing required property {}",
                    json::py_repr(&json::s(*key))
                ));
            }
        }
        let allow_additional = schema
            .get("additionalProperties")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        if !allow_additional {
            let mut extras: Vec<String> = pairs
                .iter()
                .filter(|(name, _)| !properties.iter().any(|(prop, _)| prop == name))
                .map(|(name, _)| name.clone())
                .collect();
            if !extras.is_empty() {
                extras.sort();
                let where_ = if path.is_empty() { "value" } else { path };
                let word = if extras.len() == 1 {
                    "property"
                } else {
                    "properties"
                };
                let shown = json::py_repr(&json::string_list(&extras));
                return Err(format!("{where_}: unexpected {word} {shown}"));
            }
        }
        for (key, sub) in pairs {
            let Some((_, sub_schema)) = properties.iter().find(|(name, _)| name == key) else {
                continue;
            };
            if matches!(sub, Value::Null) && !required.contains(&key.as_str()) {
                let type_spec = sub_schema.get("type");
                let allows_null = match type_spec {
                    None => true,
                    Some(Value::String(name)) => name == "null",
                    Some(Value::Array(items)) => {
                        items.iter().any(|item| item.as_str() == Some("null"))
                    }
                    _ => false,
                };
                if !allows_null {
                    continue;
                }
            }
            let sub_path = if path.is_empty() {
                key.clone()
            } else {
                format!("{path}.{key}")
            };
            validate(sub, sub_schema, &sub_path)?;
        }
    }
    if let Value::Array(items) = value {
        if let Some(item_schema) = schema.get("items") {
            for (idx, item) in items.iter().enumerate() {
                validate(item, item_schema, &format!("{path}[{idx}]"))?;
            }
        }
    }
    if let Some(n) = value.as_f64() {
        if !matches!(value, Value::Bool(_)) {
            if let Some(min) = schema.get("minimum").and_then(Value::as_f64) {
                if n < min {
                    let where_ = if path.is_empty() { "value" } else { path };
                    return Err(format!("{where_} must be >= {min} (got {n})"));
                }
            }
            if let Some(max) = schema.get("maximum").and_then(Value::as_f64) {
                if n > max {
                    let where_ = if path.is_empty() { "value" } else { path };
                    return Err(format!("{where_} must be <= {max} (got {n})"));
                }
            }
        }
    }
    if let Value::String(text) = value {
        let len = text.chars().count();
        if let Some(min) = schema.get("minLength").and_then(Value::as_i64) {
            if (len as i64) < min {
                let where_ = if path.is_empty() { "value" } else { path };
                return Err(format!("{where_} must have length >= {min} (got {len})"));
            }
        }
        if let Some(max) = schema.get("maxLength").and_then(Value::as_i64) {
            if (len as i64) > max {
                let where_ = if path.is_empty() { "value" } else { path };
                return Err(format!("{where_} must have length <= {max} (got {len})"));
            }
        }
    }
    Ok(())
}

fn type_ok(value: &Value, spec: &Value) -> bool {
    match spec {
        Value::Array(items) => items.iter().any(|item| type_ok(value, item)),
        Value::String(name) => match name.as_str() {
            "string" => matches!(value, Value::String(_)),
            "integer" => matches!(value, Value::Int(_)),
            "number" => matches!(value, Value::Int(_) | Value::Float(_)),
            "boolean" => matches!(value, Value::Bool(_)),
            "object" => matches!(value, Value::Object(_)),
            "array" => matches!(value, Value::Array(_)),
            "null" => matches!(value, Value::Null),
            _ => true,
        },
        _ => true,
    }
}

pub fn unknown_tool(schema: &Schema, name: &str) -> String {
    let mut names: Vec<String> = schema.tools.iter().map(|(n, _)| n.clone()).collect();
    names.sort();
    format!(
        "Unknown tool: {}. Available: {}",
        json::py_repr(&json::s(name)),
        json::py_repr(&json::string_list(&names))
    )
}
