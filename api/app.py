import importlib
import json
import math
import os
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

# 어떤 방식의 API를 쓸 것인지를 외부에서 환경변수를 통해 주입
MODEL_IMPL = os.getenv("MODEL_IMPL", "rule")
estimate_delivery_minutes = importlib.import_module(f"api.predictors.{MODEL_IMPL}").estimate_delivery_minutes

class EtaRequest(BaseModel):
    """배달 예상 시간 요청"""
    # 모델이 더 많은 정보를 원하면 여기에 필드를 추가
    # 아래 ge=0 와 같은 조건들을 유효성 검사 룰을 지정 (validation rule)
    # 상한은 배달 서비스 기준의 현실적인 범위. 오버플로 방지도 겸한다.
    # allow_inf_nan=False: NaN, Infinity 는 422 로 거부
    distance_km: float = Field(
        ge=0, le=50, allow_inf_nan=False, description="가게에서 배달지까지 거리(km), 0~50"
    )
    pending_orders: int = Field(ge=0, le=500, description="지금 밀려 있는 주문 수, 0~500")

class EtaResponse(BaseModel):
    """배달 예상 시간 응답"""
    minutes: float
    model: str  # 어떤 구현체가 낸 예측인지 (장애 추적용)

APP_VERSION = "0.2.0"  # pyproject.toml 의 version 과 같아야 한다 (테스트로 확인)

app = FastAPI(title="delivery-eta", version=APP_VERSION)

def _json_safe(value):
    """NaN/Infinity 처럼 JSON 으로 못 바꾸는 실수를 문자열로 바꾼다 (중첩 dict/list 포함)."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """기본 422 응답과 같은 형식. 단, 입력에 NaN/Infinity 가 있어도 500 이 나지 않게 한다."""
    return JSONResponse(
        status_code=422, content={"detail": _json_safe(jsonable_encoder(exc.errors()))}
    )

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """API 소개 페이지 (HTML)"""
    # 예시는 실제 계산 함수로 만든다 → 구현이 바뀌어도 페이지 예시가 틀리지 않는다
    example_req = {"distance_km": 2.5, "pending_orders": 3}
    example_res = eta(EtaRequest(**example_req)).model_dump()
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>delivery-eta</title></head>
<body>
  <h1>배달 예상 시간 API</h1>
  <p>버전 {APP_VERSION} · 구현체 {MODEL_IMPL}</p>
  <ul>
    <li><code>POST /eta</code> - 거리(0~50km), 대기 주문(0~500건)으로 예상 시간 계산</li>
    <li><a href="/docs">/docs</a> - API 문서</li>
    <li><a href="/health">/health</a> - 상태 확인</li>
  </ul>

  <h2>계산 방식 (rule 구현체 기준)</h2>
  <p>예상 시간(분) = 기본 10분 + 1km당 3분 + 대기 주문 1건당 2분</p>

  <h2>요청·응답 예시</h2>
  <p>요청: <code>POST /eta</code></p>
  <pre>{json.dumps(example_req, ensure_ascii=False)}</pre>
  <p>응답</p>
  <pre>{json.dumps(example_res, ensure_ascii=False)}</pre>

  <h2>배포 흐름</h2>
  <p>GitHub에 버전 태그 푸시 → GitHub Actions에서 테스트 → ghcr.io에 Docker 이미지 게시 → Render에서 배포</p>
</body></html>"""

@app.get("/health")
def health() -> dict[str, str]:
    """서비스 상태, 현재 구현체 이름, 앱 버전을 돌려준다."""
    return {"status": "ok", "model": MODEL_IMPL, "version": APP_VERSION}

@app.post("/eta", response_model=EtaResponse)
def eta(req: EtaRequest) -> EtaResponse:
    """배달 예상 시간을 계산"""
    minutes = estimate_delivery_minutes(req.distance_km, req.pending_orders)
    return EtaResponse(minutes=minutes, model=MODEL_IMPL)

