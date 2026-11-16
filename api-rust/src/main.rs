//! delivery-eta 의 Rust 구현. api/app.py 와 같은 계약(POST /eta, GET /health)을 따른다.
//! 예측은 ONNX 모델(입력 "X" [1, 2] float32 → 출력 "variable" [1, 1])을 ONNX Runtime 으로 실행한다.
//! 정규화는 모델 안에 있으므로 원래 단위의 값을 그대로 넣는다.
//!
//! 환경 변수
//!   PORT       듣는 포트 (기본 8080)
//!   MODEL_DIR  model_sklearn.onnx 가 있는 폴더 (기본 artifacts, Python 구현과 같다)
//!   ORT_LIB    libonnxruntime.so 경로 (기본 /usr/local/lib/libonnxruntime.so)

mod validate;

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use axum::{
    Json, Router,
    body::Bytes,
    extract::State,
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use ort::{session::Session, value::Tensor};
use serde_json::json;

/// 빌드할 때 pyproject.toml 의 version 을 APP_VERSION 환경 변수로 넣는다 (Dockerfile 참고).
const VERSION: &str = match option_env!("APP_VERSION") {
    Some(v) => v,
    None => "dev",
};

/// /health 와 /eta 응답의 model. 장애 추적 때 Python·Go 구현과 구분되도록 이름을 따로 둔다.
const MODEL_NAME: &str = "onnx-rust";

struct Predictor {
    // 세션이 쓰는 ONNX Runtime 환경. 서버가 끝날 때까지 살아 있도록 함께 들고 있는다.
    _env: ort::environment::Environment,
    // ort 의 Session::run 은 &mut self 를 받는다. 모델이 작아 실행이 짧으므로 Mutex 로 차례대로 실행한다.
    session: Mutex<Session>,
}

impl Predictor {
    fn new(lib_path: &str, model_path: &PathBuf) -> Result<Self, String> {
        let env = ort::init_from(lib_path)
            .map_err(|e| format!("ONNX Runtime 불러오기 실패 ({lib_path}): {e}"))?
            .with_name("eta-api")
            .build()
            .map_err(|e| format!("ONNX Runtime 초기화 실패 ({lib_path}): {e}"))?;
        let session = Session::builder(&env)
            .and_then(|mut b| b.commit_from_file(model_path))
            .map_err(|e| format!("모델 불러오기 실패 ({}): {e}", model_path.display()))?;
        Ok(Predictor { _env: env, session: Mutex::new(session) })
    }

    fn predict(&self, distance_km: f64, pending_orders: i64) -> ort::Result<f64> {
        let input = Tensor::from_array(([1usize, 2], vec![distance_km as f32, pending_orders as f32]))?;
        let mut session = self.session.lock().unwrap_or_else(|p| p.into_inner());
        let outputs = session.run(ort::inputs!["X" => input])?;
        let (_, data) = outputs["variable"].try_extract_tensor::<f32>()?;
        Ok(f64::from(data[0]))
    }
}

fn detail(status: StatusCode, msg: &str) -> Response {
    (status, Json(json!({ "detail": msg }))).into_response()
}

async fn health() -> Response {
    Json(json!({ "status": "ok", "model": MODEL_NAME, "version": VERSION })).into_response()
}

/// Json 추출기를 쓰지 않고 본문을 직접 읽는다 → 형식 오류도 FastAPI 처럼 모두 422 가 된다.
async fn eta(State(p): State<Arc<Predictor>>, headers: HeaderMap, body: Bytes) -> Response {
    // Content-Type 이 UTF-8 이 아니면 JSON 이 아닌 본문으로 본다 (이후 422)
    let content_type = match headers.get(header::CONTENT_TYPE) {
        None => None,
        Some(v) => Some(v.to_str().unwrap_or("invalid")),
    };
    let parsed = match validate::parse_body(content_type, &body) {
        Ok(v) => v,
        Err(e) => return (StatusCode::UNPROCESSABLE_ENTITY, Json(json!({ "detail": [e] }))).into_response(),
    };
    let input = match validate::validate_eta(parsed.as_ref()) {
        Ok(i) => i,
        Err(errs) => return (StatusCode::UNPROCESSABLE_ENTITY, Json(json!({ "detail": errs }))).into_response(),
    };
    match p.predict(input.distance_km, input.pending_orders) {
        Ok(minutes) => Json(json!({ "minutes": minutes, "model": MODEL_NAME })).into_response(),
        Err(e) => {
            eprintln!("예측 실패: {e}");
            detail(StatusCode::INTERNAL_SERVER_ERROR, "Internal Server Error")
        }
    }
}

/// FastAPI 처럼 다른 메서드에 405 와 {"detail": "Method Not Allowed"} 를 돌려준다.
fn method_not_allowed(allow: &'static str) -> Response {
    let mut res = detail(StatusCode::METHOD_NOT_ALLOWED, "Method Not Allowed");
    res.headers_mut().insert(header::ALLOW, header::HeaderValue::from_static(allow));
    res
}

fn router(p: Arc<Predictor>) -> Router {
    Router::new()
        .route("/health", get(health).fallback(|| async { method_not_allowed("GET") }))
        .route("/eta", post(eta).fallback(|| async { method_not_allowed("POST") }))
        .fallback(|| async { detail(StatusCode::NOT_FOUND, "Not Found") })
        .with_state(p)
}

fn getenv(key: &str, default: &str) -> String {
    std::env::var(key).ok().filter(|v| !v.is_empty()).unwrap_or_else(|| default.to_string())
}

/// docker stop(SIGTERM)이나 Ctrl+C 를 받으면 진행 중인 요청을 마치고 끝낸다.
async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let term = async {
        if let Ok(mut s) = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            s.recv().await;
        }
    };
    #[cfg(not(unix))]
    let term = std::future::pending::<()>();
    tokio::select! { _ = ctrl_c => {}, _ = term => {} }
}

#[tokio::main]
async fn main() {
    let model_path = PathBuf::from(getenv("MODEL_DIR", "artifacts")).join("model_sklearn.onnx");
    let predictor = match Predictor::new(&getenv("ORT_LIB", "/usr/local/lib/libonnxruntime.so"), &model_path) {
        Ok(p) => Arc::new(p),
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(1);
        }
    };
    let addr = format!("0.0.0.0:{}", getenv("PORT", "8080"));
    let listener = match tokio::net::TcpListener::bind(&addr).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("{addr} 에서 듣기 실패: {e}");
            std::process::exit(1);
        }
    };
    eprintln!("delivery-eta (rust) {VERSION}, model={}, listening on {addr}", model_path.display());
    if let Err(e) = axum::serve(listener, router(predictor)).with_graceful_shutdown(shutdown_signal()).await {
        eprintln!("서버 오류: {e}");
        std::process::exit(1);
    }
}
