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
        .unwrap_or("1.1.0")
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
    let _ = tool;
    validate(args, params, "")
}

fn merged_enum(errors: &[String]) -> Option<String> {
    let mut values = Vec::new();
    let mut path = None;
    let mut got = None;
    for err in errors {
        let Some(rest) = err.split(" must be one of ").nth(1) else {
            return None;
        };
        let (list, sample) = rest.split_once(" (got ")?;
        let field = err.split(" must be one of ").next()?.trim();
        if path.get_or_insert(field.to_string()) != field {
            return None;
        }
        let sample = sample.trim_end_matches(')');
        if got.get_or_insert(sample.to_string()) != sample {
            return None;
        }
        let inner = list.trim().trim_start_matches('[').trim_end_matches(']');
        for item in inner.split(',') {
            let item = item.trim();
            if !item.is_empty() && !values.iter().any(|seen: &String| seen == item) {
                values.push(item.to_string());
            }
        }
    }
    Some(format!(
        "{} must be one of [{}] (got {})",
        path?,
        values.join(", "),
        got?
    ))
}

pub fn validate(value: &Value, schema: &Value, path: &str) -> Result<(), String> {
    if let Some(Value::Array(branches)) = schema.get("oneOf") {
        let mut matches = 0;
        let mut errors = Vec::new();
        for branch in branches {
            match validate(value, branch, path) {
                Ok(()) => matches += 1,
                Err(err) => errors.push(err),
            }
        }
        if matches != 1 {
            if matches == 0 {
                if let Some(err) = merged_enum(&errors) {
                    return Err(err);
                }
                if let Some(err) = errors.last() {
                    return Err(err.clone());
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
            let expected = match type_spec {
                Value::String(name) => format!("'{name}'"),
                other => json::py_repr(other),
            };
            return Err(format!("{} is not of type {expected}", json::py_repr(value)));
        }
    }
    if let Some(Value::Array(enum_vals)) = schema.get("enum") {
        if !enum_vals.iter().any(|item| item == value) {
            return Err(format!(
                "{} is not one of {}",
                json::py_repr(value),
                json::py_repr(&Value::Array(enum_vals.clone()))
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
                return Err(format!(
                    "{} is a required property",
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
                if min == 1 && text.is_empty() {
                    return Err("'' should be non-empty".into());
                }
                return Err(format!("{} is too short", json::py_repr(&json::s(text))));
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
            // jsonschema (the `mcp` library's check) treats 3.0 as an integer.
            "integer" => match value {
                Value::Int(_) => true,
                Value::Float(n) => n.is_finite() && n.fract() == 0.0,
                _ => false,
            },
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

/// Names the unknown tool and up to three close matches instead of listing
/// every tool: `tools/list` already has the list, and the full list cost an
/// agent over a kilobyte per mistyped name.
pub fn unknown_tool(schema: &Schema, name: &str) -> String {
    let wanted = name.trim().to_ascii_lowercase().replace('-', "_");
    let mut close: Vec<(usize, &str)> = schema
        .tools
        .iter()
        .map(|(tool, _)| tool.as_str())
        .chain(std::iter::once("who"))
        .filter_map(|tool| {
            let distance = edit_distance(&wanted, tool);
            let near = distance <= (wanted.len().max(tool.len()) / 3).max(2);
            let part = wanted.len() >= 4 && (tool.contains(wanted.as_str()) || wanted.contains(tool));
            let word = wanted
                .split('_')
                .filter(|word| word.len() >= 4)
                .any(|word| tool.split('_').any(|other| other == word));
            (near || part || word).then_some((distance, tool))
        })
        .collect();
    close.sort();
    close.truncate(3);
    let hint = if close.is_empty() {
        String::new()
    } else {
        let names: Vec<String> = close.iter().map(|(_, tool)| format!("'{tool}'")).collect();
        format!("Did you mean {}? ", names.join(", "))
    };
    format!(
        "Unknown tool: {}. {hint}tools/list names every tool this server offers.",
        json::py_repr(&json::s(name))
    )
}

fn edit_distance(left: &str, right: &str) -> usize {
    let right: Vec<char> = right.chars().collect();
    let mut row: Vec<usize> = (0..=right.len()).collect();
    for (i, a) in left.chars().enumerate() {
        let mut diagonal = row[0];
        row[0] = i + 1;
        for (j, b) in right.iter().enumerate() {
            let above = row[j + 1];
            row[j + 1] = (above + 1).min(row[j] + 1).min(diagonal + usize::from(a != *b));
            diagonal = above;
        }
    }
    row[right.len()]
}
