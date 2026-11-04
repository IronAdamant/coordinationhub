//! JSON Schema subset used by the Python tool boundary.

use crate::json::{self, Json};
use std::sync::OnceLock;

const TOOLS_JSON: &str = include_str!("../assets/tools.json");

pub fn tools_version() -> String {
    schema_root()
        .get("tools_version")
        .and_then(|v| v.as_str())
        .unwrap_or("1.0.0")
        .to_string()
}

pub fn schema_root() -> &'static Json {
    static ROOT: OnceLock<Json> = OnceLock::new();
    ROOT.get_or_init(|| json::parse(TOOLS_JSON).expect("assets/tools.json"))
}

pub fn tool_schemas() -> &'static Json {
    schema_root().get("schemas").expect("schemas")
}

pub fn parity_tool_names() -> Vec<String> {
    let mut names: Vec<String> = match tool_schemas() {
        Json::Object(pairs) => pairs.iter().map(|(k, _)| k.clone()).collect(),
        _ => Vec::new(),
    };
    names.sort();
    names
}

pub fn phase2_names() -> &'static [&'static str] {
    &[
        "claim_work",
        "compact_status",
        "inbox",
        "list_work",
        "release_work",
    ]
}

pub fn all_tool_names() -> Vec<String> {
    let mut names = parity_tool_names();
    for n in phase2_names() {
        names.push((*n).to_string());
    }
    names.sort();
    names
}

pub fn validate_tool(name: &str, args: &Json) -> Result<(), String> {
    if phase2_names().contains(&name) {
        return validate_phase2(name, args);
    }
    let Some(schema) = tool_schemas().get(name) else {
        return Ok(());
    };
    let Some(params) = schema.get("parameters") else {
        return Ok(());
    };
    validate(args, params, "").map_err(|e| format!("{name}: {e}"))
}

fn validate_phase2(name: &str, args: &Json) -> Result<(), String> {
    let required: &[&str] = match name {
        "claim_work" | "release_work" => &["agent_id", "area"],
        "inbox" => &["agent_id"],
        "compact_status" | "list_work" => &[],
        _ => &[],
    };
    if !matches!(args, Json::Object(_)) {
        return Err(format!("{name}: value must be of type 'object' (got {})", args.ty_name()));
    }
    for key in required {
        if args.get(key).is_none() {
            return Err(format!(
                "{name}: value: missing required property '{key}'"
            ));
        }
    }
    Ok(())
}

fn validate(value: &Json, schema: &Json, path: &str) -> Result<(), String> {
    if let Some(one_of) = schema.get("oneOf").and_then(|v| v.as_array()) {
        let mut matches = 0;
        let mut last = None;
        for sub in one_of {
            match validate(value, sub, path) {
                Ok(()) => matches += 1,
                Err(e) => last = Some(e),
            }
        }
        if matches != 1 {
            if matches == 0 {
                if let Some(e) = last {
                    return Err(e);
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
                py_repr(type_spec),
                value.ty_name()
            ));
        }
    }
    if let Some(enum_v) = schema.get("enum").and_then(|v| v.as_array()) {
        if !enum_v.iter().any(|c| json_eq(c, value)) {
            let where_ = if path.is_empty() { "value" } else { path };
            return Err(format!(
                "{where_} must be one of {} (got {})",
                py_list(enum_v),
                py_repr(value)
            ));
        }
    }
    if let Json::Object(pairs) = value {
        let properties = schema.get("properties");
        let required = schema
            .get("required")
            .and_then(|v| v.as_array())
            .map(|a| a.to_vec())
            .unwrap_or_default();
        for req in &required {
            let Some(key) = req.as_str() else { continue };
            if !pairs.iter().any(|(k, _)| k == key) {
                let where_ = if path.is_empty() { "value" } else { path };
                return Err(format!(
                    "{where_}: missing required property '{}'",
                    key
                ));
            }
        }
        let allow_extra = schema
            .get("additionalProperties")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        if !allow_extra {
            if let Some(Json::Object(props)) = properties {
                let extras: Vec<&str> = pairs
                    .iter()
                    .filter(|(k, _)| !props.iter().any(|(p, _)| p == k))
                    .map(|(k, _)| k.as_str())
                    .collect();
                if !extras.is_empty() {
                    let mut sorted = extras.iter().map(|s| (*s).to_string()).collect::<Vec<_>>();
                    sorted.sort();
                    let where_ = if path.is_empty() { "value" } else { path };
                    let word = if sorted.len() == 1 { "property" } else { "properties" };
                    return Err(format!(
                        "{where_}: unexpected {word} {}",
                        py_str_list(&sorted)
                    ));
                }
            }
        }
        if let Some(Json::Object(props)) = properties {
            for (key, sub) in pairs {
                let Some((_, sub_schema)) = props.iter().find(|(k, _)| k == key) else {
                    continue;
                };
                let is_required = required.iter().any(|r| r.as_str() == Some(key));
                if sub.is_null() && !is_required {
                    let type_spec = sub_schema.get("type");
                    let allows_null = match type_spec {
                        None => true,
                        Some(Json::String(t)) => t == "null",
                        Some(Json::Array(ts)) => ts.iter().any(|t| t.as_str() == Some("null")),
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
    }
    if let Json::Array(items) = value {
        if let Some(item_schema) = schema.get("items") {
            for (i, item) in items.iter().enumerate() {
                validate(item, item_schema, &format!("{path}[{i}]"))?;
            }
        }
    }
    if let Some(n) = value.as_f64() {
        if !matches!(value, Json::Bool(_)) {
            if let Some(min) = schema.get("minimum").and_then(|v| v.as_f64()) {
                if n < min {
                    let where_ = if path.is_empty() { "value" } else { path };
                    return Err(format!("{where_} must be >= {min} (got {n})"));
                }
            }
            if let Some(max) = schema.get("maximum").and_then(|v| v.as_f64()) {
                if n > max {
                    let where_ = if path.is_empty() { "value" } else { path };
                    return Err(format!("{where_} must be <= {max} (got {n})"));
                }
            }
        }
    }
    if let Json::String(s) = value {
        let len = s.chars().count();
        if let Some(min) = schema.get("minLength").and_then(|v| v.as_i64()) {
            if len < min as usize {
                let where_ = if path.is_empty() { "value" } else { path };
                return Err(format!(
                    "{where_} must have length >= {min} (got {len})"
                ));
            }
        }
        if let Some(max) = schema.get("maxLength").and_then(|v| v.as_i64()) {
            if len > max as usize {
                let where_ = if path.is_empty() { "value" } else { path };
                return Err(format!(
                    "{where_} must have length <= {max} (got {len})"
                ));
            }
        }
    }
    Ok(())
}

fn type_ok(value: &Json, spec: &Json) -> bool {
    match spec {
        Json::Array(items) => items.iter().any(|s| type_ok(value, s)),
        Json::String(t) => match t.as_str() {
            "string" => matches!(value, Json::String(_)),
            "integer" => matches!(value, Json::Int(_)),
            "number" => matches!(value, Json::Int(_) | Json::Float(_)),
            "boolean" => matches!(value, Json::Bool(_)),
            "object" => matches!(value, Json::Object(_)),
            "array" => matches!(value, Json::Array(_)),
            "null" => matches!(value, Json::Null),
            _ => true,
        },
        _ => true,
    }
}

fn json_eq(a: &Json, b: &Json) -> bool {
    match (a, b) {
        (Json::Int(x), Json::Float(y)) | (Json::Float(y), Json::Int(x)) => *x as f64 == *y,
        _ => a == b,
    }
}

pub fn py_repr(v: &Json) -> String {
    match v {
        Json::Null => "None".into(),
        Json::Bool(true) => "True".into(),
        Json::Bool(false) => "False".into(),
        Json::Int(n) => n.to_string(),
        Json::Float(n) => n.to_string(),
        Json::String(s) => format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'")),
        Json::Array(a) => py_list(a),
        Json::Object(_) => v.dump(),
    }
}

fn py_list(items: &[Json]) -> String {
    let parts: Vec<String> = items.iter().map(py_repr).collect();
    format!("[{}]", parts.join(", "))
}

fn py_str_list(items: &[String]) -> String {
    let parts: Vec<String> = items.iter().map(|s| format!("'{s}'")).collect();
    format!("[{}]", parts.join(", "))
}
