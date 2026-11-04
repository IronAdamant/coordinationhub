//! Argument readers and small shared helpers.

use crate::json::Json;
use std::env;

pub fn env_i64(name: &str, default: i64) -> i64 {
    env::var(name)
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(default)
}

pub fn env_f64(name: &str, default: f64) -> f64 {
    env::var(name)
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(default)
}

pub fn truncate(value: &str, max_len: usize) -> String {
    if value.chars().count() <= max_len {
        return value.to_string();
    }
    let original = value.chars().count();
    let keep = max_len.saturating_sub(32);
    let head: String = value.chars().take(keep).collect();
    format!("{head}... [truncated {original}->{max_len}]")
}

pub fn truncate_opt(value: Option<&str>, max_len: usize) -> Option<String> {
    value.map(|v| truncate(v, max_len))
}

pub fn arg_str<'a>(args: &'a Json, key: &str) -> Option<&'a str> {
    args.get(key).and_then(|v| v.as_str())
}

pub fn arg_string(args: &Json, key: &str) -> Option<String> {
    arg_str(args, key).map(|s| s.to_string())
}

pub fn arg_f64(args: &Json, key: &str, default: f64) -> f64 {
    args.get(key).and_then(|v| v.as_f64()).unwrap_or(default)
}

pub fn arg_opt_f64(args: &Json, key: &str) -> Option<f64> {
    match args.get(key) {
        Some(Json::Null) | None => None,
        Some(v) => v.as_f64(),
    }
}

pub fn arg_i64(args: &Json, key: &str, default: i64) -> i64 {
    args.get(key).and_then(|v| v.as_i64()).unwrap_or(default)
}

pub fn arg_opt_i64(args: &Json, key: &str) -> Option<i64> {
    match args.get(key) {
        Some(Json::Null) | None => None,
        Some(v) => v.as_i64(),
    }
}

pub fn arg_bool(args: &Json, key: &str, default: bool) -> bool {
    args.get(key).and_then(|v| v.as_bool()).unwrap_or(default)
}

pub fn arg_strs(args: &Json, key: &str) -> Option<Vec<String>> {
    match args.get(key) {
        None | Some(Json::Null) => None,
        Some(Json::Array(items)) => Some(
            items
                .iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect(),
        ),
        Some(_) => None,
    }
}

pub fn regions_overlap(
    a0: Option<i64>,
    a1: Option<i64>,
    b0: Option<i64>,
    b1: Option<i64>,
) -> bool {
    match (a0, b0) {
        (Some(a_start), Some(b_start)) => {
            let a_end = a1.unwrap_or(a_start);
            let b_end = b1.unwrap_or(b_start);
            a_start < b_end && b_start < a_end
        }
        _ => true,
    }
}

pub fn obj(pairs: Vec<(&str, Json)>) -> Json {
    Json::object(pairs)
}

pub fn arr_json(items: Vec<Json>) -> Json {
    Json::Array(items)
}
