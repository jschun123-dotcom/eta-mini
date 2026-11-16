# delivery-eta Rust 서비스. 저장소 루트를 빌드 컨텍스트로 쓴다.
#   docker build -f api-rust/Dockerfile -t eta-api-rust .
#   docker run --rm -p 8082:8080 eta-api-rust
# artifacts/model_sklearn.onnx 가 먼저 있어야 한다 (uv run --group train python -m training.export_onnx).

# ── 1. ONNX Runtime 받기 ─────────────────────────────────────────────
# 버전은 uv.lock 의 onnxruntime 버전을 읽어 쓴다 → Python 구현과 같은 런타임으로 실행된다.
FROM debian:bookworm-slim AS ort
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY uv.lock /tmp/uv.lock
RUN set -eu; \
    ORT_VERSION="$(awk '/^name = "onnxruntime"$/ {f=1; next} f && /^version = / {gsub(/"/, "", $3); print $3; exit}' /tmp/uv.lock)"; \
    test -n "$ORT_VERSION" || { echo "uv.lock 에서 onnxruntime 버전을 찾지 못함" >&2; exit 1; }; \
    case "$TARGETARCH" in \
      amd64) ORT_ARCH=x64 ;; \
      arm64) ORT_ARCH=aarch64 ;; \
      *) echo "지원하지 않는 TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    echo "onnxruntime $ORT_VERSION ($ORT_ARCH)"; \
    curl -fsSL "https://github.com/microsoft/onnxruntime/releases/download/v${ORT_VERSION}/onnxruntime-linux-${ORT_ARCH}-${ORT_VERSION}.tgz" \
      | tar -xz -C /tmp; \
    cp -L "/tmp/onnxruntime-linux-${ORT_ARCH}-${ORT_VERSION}/lib/libonnxruntime.so" /libonnxruntime.so

# ── 2. Rust 빌드 ────────────────────────────────────────────────────
# ort 는 load-dynamic 기능으로 libonnxruntime.so 를 실행 중에 불러온다 (빌드에는 .so 가 필요 없고, 내려받지도 않는다).
FROM rust:1.100-bookworm AS build
WORKDIR /src
# 의존성만 먼저 빌드해 캐시한다 → 소스만 바뀌면 이 단계를 다시 하지 않는다
COPY api-rust/Cargo.toml api-rust/Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs \
    && cargo build --release --locked \
    && rm -rf src
COPY api-rust/src ./src
COPY pyproject.toml /tmp/pyproject.toml
# 앱 버전도 숫자를 적지 않고 pyproject.toml 에서 읽는다 (/health 의 version 이 Python 구현과 같아진다)
RUN set -eu; \
    APP_VERSION="$(awk -F '"' '/^version = / {print $2; exit}' /tmp/pyproject.toml)"; \
    touch src/main.rs; \
    cargo test --release --locked; \
    APP_VERSION="$APP_VERSION" cargo build --release --locked; \
    cp target/release/eta-api /eta-api

# ── 3. 실행 이미지 ──────────────────────────────────────────────────
FROM debian:bookworm-slim
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=ort /libonnxruntime.so /usr/local/lib/libonnxruntime.so
COPY --from=build /eta-api /usr/local/bin/eta-api
COPY artifacts/model_sklearn.onnx artifacts/model_sklearn.onnx
ENV PORT=8080 \
    MODEL_DIR=/app/artifacts \
    ORT_LIB=/usr/local/lib/libonnxruntime.so
USER app
EXPOSE 8080
CMD ["eta-api"]
