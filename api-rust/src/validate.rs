//! 요청 본문 해석과 검증. api/app.py 의 EtaRequest(Pydantic lax 모드)와 같은 규칙을 따른다.
//! 기준은 api/tests/test_app.py 에 적힌 계약이다 (api-go/validate.go 와 같은 규칙).
//!
//!   - 숫자 문자열("2.5"), 소수부가 0인 실수 주문 수(2.0), bool(→1/0)은 받아들인다
//!   - NaN·Infinity 는 422 (Python json 은 NaN/Infinity 토큰을 읽으므로 여기서도 읽은 뒤 거부한다)
//!   - 범위(거리 0~50, 주문 0~500)를 벗어나면 422, 모르는 필드는 무시한다
//!   - JSON 이 아닌 본문, 깨진 JSON, 빈 본문도 모두 422 다 (axum Json 추출기의 400·415 를 쓰지 않는다)
//!   - 422 본문은 FastAPI 와 같은 {"detail": [{"type", "loc", "msg", "input", "ctx"?}]} 형식이다

use serde::Serialize;
use serde_json::{Map, Value, json};

/// FastAPI 422 응답의 detail 항목 하나.
#[derive(Debug, Serialize)]
pub struct ValErr {
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub loc: Vec<Value>,
    pub msg: String,
    pub input: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ctx: Option<Value>,
}

impl ValErr {
    fn new(kind: &'static str, msg: impl Into<String>) -> Self {
        ValErr { kind, loc: Vec::new(), msg: msg.into(), input: Value::Null, ctx: None }
    }

    fn at(mut self, field: &str, input: &Value) -> Self {
        self.loc = vec![json!("body"), json!(field)];
        self.input = for_output(input);
        self
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EtaInput {
    pub distance_km: f64,
    pub pending_orders: i64,
}

// serde_json 은 NaN/Infinity 토큰을 모른다. 파싱 전에 문자열 바깥의 토큰을 이 표식이 붙은 문자열로 바꾼다.
// 실제 요청 문자열과 겹치지 않도록 NUL 문자로 시작한다.
const NON_FINITE_PREFIX: &str = "\u{0}nonfinite:";
const NON_FINITE_TOKENS: [&str; 3] = ["NaN", "Infinity", "-Infinity"];

/// FastAPI 처럼 Content-Type 에 따라 본문을 JSON 으로 읽는다.
/// 본문이 없으면 None (이후 "missing"), JSON 으로 읽지 않는 경우(form 등)는 본문 문자열 자체를 값으로
/// 돌려줘 이후 검증에서 422 가 나게 한다.
pub fn parse_body(content_type: Option<&str>, body: &[u8]) -> Result<Option<Value>, ValErr> {
    let is_json = match content_type {
        None => true,
        Some(ct) if ct.trim().is_empty() => true,
        Some(ct) => {
            let mt = ct.split(';').next().unwrap_or("").trim().to_ascii_lowercase();
            mt == "application/json" || (mt.starts_with("application/") && mt.ends_with("+json"))
        }
    };
    if body.is_empty() {
        return Ok(None);
    }
    if !is_json {
        return Ok(Some(Value::String(String::from_utf8_lossy(body).into_owned())));
    }
    let replaced = replace_non_finite_tokens(body);
    serde_json::from_slice::<Value>(&replaced).map(Some).map_err(|e| json_invalid(&replaced, &e))
}

fn json_invalid(body: &[u8], e: &serde_json::Error) -> ValErr {
    // serde_json 은 줄·칸(1부터)을 준다. Python 처럼 본문 앞에서부터의 위치(0부터)로 바꾼다.
    // (NaN 토큰을 바꿔 넣은 본문 기준이라 조금 어긋날 수 있다)
    let line_start: usize = body
        .split(|&b| b == b'\n')
        .take(e.line().saturating_sub(1))
        .map(|l| l.len() + 1)
        .sum();
    let pos = (line_start + e.column()).saturating_sub(1);
    let reason = if e.to_string().starts_with("trailing characters") {
        "Extra data".to_string()
    } else {
        e.to_string()
    };
    ValErr {
        kind: "json_invalid",
        loc: vec![json!("body"), json!(pos)],
        msg: "JSON decode error".into(),
        input: json!({}),
        ctx: Some(json!({ "error": reason })),
    }
}

/// 문자열 바깥의 NaN, Infinity, -Infinity 토큰을 표식 문자열로 바꾼다.
fn replace_non_finite_tokens(b: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(b.len());
    let (mut in_str, mut esc) = (false, false);
    let mut i = 0;
    while i < b.len() {
        let c = b[i];
        if in_str {
            out.push(c);
            if esc {
                esc = false;
            } else if c == b'\\' {
                esc = true;
            } else if c == b'"' {
                in_str = false;
            }
            i += 1;
            continue;
        }
        if c == b'"' {
            in_str = true;
            out.push(c);
            i += 1;
            continue;
        }
        if let Some(tok) = NON_FINITE_TOKENS.iter().find(|t| b[i..].starts_with(t.as_bytes())) {
            out.extend_from_slice(format!("\"\\u0000nonfinite:{tok}\"").as_bytes());
            i += tok.len();
        } else {
            out.push(c);
            i += 1;
        }
    }
    out
}

fn non_finite(v: &Value) -> Option<f64> {
    match v.as_str()?.strip_prefix(NON_FINITE_PREFIX)? {
        "NaN" => Some(f64::NAN),
        "-Infinity" => Some(f64::NEG_INFINITY),
        _ => Some(f64::INFINITY),
    }
}

/// 오류 응답의 input 용 사본. FastAPI 의 _json_safe 처럼 비유한 수를 "NaN" 같은 문자열로 내보낸다.
fn for_output(v: &Value) -> Value {
    match v {
        Value::String(s) => match s.strip_prefix(NON_FINITE_PREFIX) {
            Some(tok) => Value::String(tok.to_string()),
            None => v.clone(),
        },
        Value::Object(m) => Value::Object(m.iter().map(|(k, e)| (k.clone(), for_output(e))).collect()),
        Value::Array(a) => Value::Array(a.iter().map(for_output).collect()),
        _ => v.clone(),
    }
}

/// 파싱한 본문을 EtaRequest 규칙으로 검증한다. 오류는 필드 순서대로 모두 모은다.
pub fn validate_eta(body: Option<&Value>) -> Result<EtaInput, Vec<ValErr>> {
    let Some(body) = body else {
        let mut e = ValErr::new("missing", "Field required");
        e.loc = vec![json!("body")];
        return Err(vec![e]);
    };
    let Value::Object(obj) = body else {
        let mut e = ValErr::new(
            "model_attributes_type",
            "Input should be a valid dictionary or object to extract fields from",
        );
        e.loc = vec![json!("body")];
        e.input = for_output(body);
        return Err(vec![e]);
    };

    let mut errs = Vec::new();
    let mut input = EtaInput { distance_km: 0.0, pending_orders: 0 };
    match obj.get("distance_km") {
        None => errs.push(missing("distance_km", obj)),
        Some(v) => match check_float(v, 0, 50) {
            Ok(f) => input.distance_km = f,
            Err(e) => errs.push(e.at("distance_km", v)),
        },
    }
    match obj.get("pending_orders") {
        None => errs.push(missing("pending_orders", obj)),
        Some(v) => match check_int(v, 0, 500) {
            Ok(n) => input.pending_orders = n,
            Err(e) => errs.push(e.at("pending_orders", v)),
        },
    }
    if errs.is_empty() { Ok(input) } else { Err(errs) }
}

fn missing(field: &str, obj: &Map<String, Value>) -> ValErr {
    ValErr::new("missing", "Field required").at(field, &Value::Object(obj.clone()))
}

fn float_type() -> ValErr {
    ValErr::new("float_type", "Input should be a valid number")
}
fn float_parsing() -> ValErr {
    ValErr::new("float_parsing", "Input should be a valid number, unable to parse string as a number")
}
fn int_type() -> ValErr {
    ValErr::new("int_type", "Input should be a valid integer")
}
fn int_parsing() -> ValErr {
    ValErr::new("int_parsing", "Input should be a valid integer, unable to parse string as an integer")
}
fn int_from_float() -> ValErr {
    ValErr::new("int_from_float", "Input should be a valid integer, got a number with a fractional part")
}
fn finite_number() -> ValErr {
    ValErr::new("finite_number", "Input should be a finite number")
}

fn range_err(below: bool, bound: i64) -> ValErr {
    let (kind, word, key) = if below {
        ("greater_than_equal", "greater", "ge")
    } else {
        ("less_than_equal", "less", "le")
    };
    let mut e = ValErr::new(kind, format!("Input should be {word} than or equal to {bound}"));
    e.ctx = Some(json!({ key: bound }));
    e
}

/// float 필드 (allow_inf_nan=False, ge, le).
fn check_float(v: &Value, ge: i64, le: i64) -> Result<f64, ValErr> {
    let f = match v {
        Value::Bool(b) => f64::from(u8::from(*b)),
        // 범위를 넘는 수(1e400)는 Rust 가 ±inf 로 읽으므로 아래에서 finite_number 가 된다 (Python 과 같다)
        Value::Number(n) => n.to_string().parse::<f64>().map_err(|_| float_parsing())?,
        Value::String(s) => match non_finite(v) {
            Some(f) => f,
            None => parse_float_string(s).ok_or_else(float_parsing)?,
        },
        _ => return Err(float_type()), // null, 배열, 객체
    };
    if !f.is_finite() {
        return Err(finite_number());
    }
    if f < ge as f64 {
        return Err(range_err(true, ge));
    }
    if f > le as f64 {
        return Err(range_err(false, le));
    }
    Ok(f)
}

/// int 필드 (ge, le). 큰 정수는 i128 로 포화시켜 비교해 오버플로 없이 422 를 낸다.
fn check_int(v: &Value, ge: i64, le: i64) -> Result<i64, ValErr> {
    let n: i128 = match v {
        Value::Bool(b) => i128::from(*b),
        Value::Number(num) => int_from_number(&num.to_string())?,
        Value::String(s) => {
            if non_finite(v).is_some() {
                return Err(finite_number());
            }
            parse_int_string(s).ok_or_else(int_parsing)?
        }
        _ => return Err(int_type()),
    };
    if n < i128::from(ge) {
        return Err(range_err(true, ge));
    }
    if n > i128::from(le) {
        return Err(range_err(false, le));
    }
    Ok(n as i64) // 위에서 범위를 확인했으므로 잘리지 않는다
}

/// JSON 숫자 → 정수. 정수 표기면 그대로, 실수 표기면 소수부가 0일 때만 받는다.
fn int_from_number(s: &str) -> Result<i128, ValErr> {
    if !s.contains(['.', 'e', 'E']) {
        return parse_int_digits(s).ok_or_else(int_parsing);
    }
    let f: f64 = s.parse().map_err(|_| int_parsing())?;
    if !f.is_finite() {
        return Err(finite_number());
    }
    if f.fract() != 0.0 {
        return Err(int_from_float());
    }
    Ok(f as i128) // as 는 포화 변환이다 (1e300 → i128::MAX)
}

/// 부호 + 숫자만 받는다. 자릿수가 너무 많으면 i128 끝값으로 포화시킨다 (범위 비교에는 충분하다).
fn parse_int_digits(s: &str) -> Option<i128> {
    let (neg, digits) = match s.as_bytes().first()? {
        b'-' => (true, &s[1..]),
        b'+' => (false, &s[1..]),
        _ => (false, s),
    };
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let trimmed = digits.trim_start_matches('0');
    let mag: i128 = if trimmed.len() > 30 { i128::MAX } else { trimmed.parse().unwrap_or(0) };
    Some(if neg { -mag } else { mag })
}

/// 밑줄은 숫자 사이에 하나씩만 올 수 있다 (Python/Pydantic 과 같다: "1_000" 가능, "_1", "1__0", "1_" 불가).
fn strip_underscores(s: &str) -> Option<String> {
    let b = s.as_bytes();
    for (i, &c) in b.iter().enumerate() {
        if c == b'_' {
            let prev_ok = i > 0 && b[i - 1].is_ascii_digit();
            let next_ok = i + 1 < b.len() && b[i + 1].is_ascii_digit();
            if !(prev_ok && next_ok) {
                return None;
            }
        }
    }
    Some(s.replace('_', ""))
}

/// 숫자 문자열 → 실수. "inf"/"nan" 은 읽은 뒤 finite 검사에서 거부된다.
/// Pydantic 은 앞뒤 공백과 밑줄을 각각은 받지만 둘이 함께 있으면(" 1_0 ") 거부한다 (정수는 둘 다 받는다).
fn parse_float_string(s: &str) -> Option<f64> {
    let trimmed = s.trim();
    if trimmed != s && s.contains('_') {
        return None;
    }
    strip_underscores(trimmed)?.parse::<f64>().ok()
}

/// 정수 문자열: 부호 + 숫자, 그리고 "3.0" 처럼 0 으로만 된 소수부는 허용한다 ("3.5", "1e1" 은 불가).
fn parse_int_string(s: &str) -> Option<i128> {
    let s = strip_underscores(s.trim())?;
    let (int_part, frac) = match s.split_once('.') {
        Some((i, f)) => (i, f),
        None => (s.as_str(), ""),
    };
    if !frac.bytes().all(|b| b == b'0') {
        return None;
    }
    parse_int_digits(int_part)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(body: &str) -> Result<EtaInput, Vec<ValErr>> {
        let parsed = parse_body(Some("application/json"), body.as_bytes()).map_err(|e| vec![e])?;
        validate_eta(parsed.as_ref())
    }

    fn bad_fields(body: &str) -> Vec<String> {
        run(body).unwrap_err().iter().map(|e| e.loc.iter().map(|l| l.to_string()).collect::<Vec<_>>().join("/")).collect()
    }

    #[test]
    fn accepts_valid_and_lenient_inputs() {
        let ok = |d: f64, o: i64| Ok::<_, ()>(EtaInput { distance_km: d, pending_orders: o });
        assert_eq!(run(r#"{"distance_km": 2.5, "pending_orders": 3}"#).map_err(|_| ()), ok(2.5, 3));
        assert_eq!(run(r#"{"distance_km": "2.5", "pending_orders": "3"}"#).map_err(|_| ()), ok(2.5, 3));
        assert_eq!(run(r#"{"distance_km": 1, "pending_orders": 2.0}"#).map_err(|_| ()), ok(1.0, 2));
        assert_eq!(run(r#"{"distance_km": true, "pending_orders": false}"#).map_err(|_| ()), ok(1.0, 0));
        assert_eq!(run(r#"{"distance_km": -0.0, "pending_orders": 0}"#).map_err(|_| ()), ok(0.0, 0));
        assert_eq!(run(r#"{"distance_km": 50, "pending_orders": 500, "foo": 1}"#).map_err(|_| ()), ok(50.0, 500));
        assert_eq!(run(r#"{"distance_km": "1_0", "pending_orders": " 3.0 "}"#).map_err(|_| ()), ok(10.0, 3));
    }

    #[test]
    fn rejects_invalid_inputs_with_field_loc() {
        let cases = [
            (r#"{"distance_km": -0.1, "pending_orders": 0}"#, "\"body\"/\"distance_km\""),
            (r#"{"distance_km": 1, "pending_orders": -1}"#, "\"body\"/\"pending_orders\""),
            (r#"{"distance_km": "far", "pending_orders": 1}"#, "\"body\"/\"distance_km\""),
            (r#"{"distance_km": 1, "pending_orders": 1.5}"#, "\"body\"/\"pending_orders\""),
            (r#"{"distance_km": null, "pending_orders": 1}"#, "\"body\"/\"distance_km\""),
            (r#"{"distance_km": 1}"#, "\"body\"/\"pending_orders\""),
            (r#"{"distance_km": 50.001, "pending_orders": 0}"#, "\"body\"/\"distance_km\""),
            (r#"{"distance_km": 1e308, "pending_orders": 0}"#, "\"body\"/\"distance_km\""),
            (r#"{"distance_km": 1e400, "pending_orders": 0}"#, "\"body\"/\"distance_km\""),
            (r#"{"distance_km": NaN, "pending_orders": 1}"#, "\"body\"/\"distance_km\""),
            (r#"{"distance_km": -Infinity, "pending_orders": 1}"#, "\"body\"/\"distance_km\""),
            (r#"{"distance_km": NaN}"#, "\"body\"/\"distance_km\""),
        ];
        for (body, loc) in cases {
            assert!(bad_fields(body).contains(&loc.to_string()), "{body}");
        }
        let big = format!(r#"{{"distance_km": 1, "pending_orders": 1{}}}"#, "0".repeat(400));
        assert!(bad_fields(&big).contains(&"\"body\"/\"pending_orders\"".to_string()));
    }

    #[test]
    fn rejects_bad_bodies() {
        for body in ["abc", "[1, 2]", "{}", r#"{"distance_km": 1, "pending_orders": 1} x"#] {
            assert!(run(body).is_err(), "{body}");
        }
        assert!(validate_eta(None).is_err());
        let form = parse_body(Some("application/x-www-form-urlencoded"), b"distance_km=1&pending_orders=1").unwrap();
        assert!(validate_eta(form.as_ref()).is_err());
    }

    #[test]
    fn non_finite_input_is_reported_as_string() {
        let errs = run(r#"{"distance_km": NaN}"#).unwrap_err();
        let out = serde_json::to_string(&errs).unwrap();
        assert!(out.contains(r#""input":"NaN""#) && !out.contains("nonfinite"), "{out}");
    }
}
