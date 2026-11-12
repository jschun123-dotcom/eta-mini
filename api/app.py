import importlib
import os
from fastapi import FastAPI
from pydantic import BaseModel, Field

# 어떤 방식의 API를 쓸 것인지를 외부에서 환경변수를 통해 주입
MODEL_IMPL = os.getenv("MODEL_IMPL", "rule")
estimate_delivery_minutes = importlib.import_module(f"api.predictors.{MODEL_IMPL}").estimate_delivery_minutes

class EtaRequest(BaseModel):
    """배달 예상 시간 요청"""
    # 모델이 더 많은 정보를 원하면 여기에 필드를 추가
    # 아래 ge=0 와 같은 조건들을 유효성 검사 룰을 지정 (validation rule)
    distance_km: float = Field(ge=0, description="가게에서 배달지까지 거리(km)")
    pending_orders: int = Field(ge=0, description="지금 밀려 있는 주문 수")

class EtaResponse(BaseModel):
    """배달 예상 시간 응답"""
    minutes: float
    model: str  # 어떤 구현체가 낸 예측인지 (장애 추적용)

app = FastAPI(title="delivery-eta")

@app.get("/health")
def health() -> dict[str, str]:
    """서비스 상태와 현재 구현체 이름을 돌려준다."""
    return {"status": "ok", "model": MODEL_IMPL}

@app.post("/eta", response_model=EtaResponse)
def eta(req: EtaRequest) -> EtaResponse:
    """배달 예상 시간을 계산"""
    minutes = estimate_delivery_minutes(req.distance_km, req.pending_orders)
    return EtaResponse(minutes=minutes, model=MODEL_IMPL)

