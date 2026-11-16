import os
from collections.abc import Sequence
from functools import cache
import torch
from torch import nn

class WithNormalize(nn.Module):
    """정규화를 모델의 일부로 포함시키는 래퍼.
    mean/std 를 버퍼로 등록하므로 state_dict 와 ONNX 내보내기에 함께 저장
    덕분에 서빙 쪽은 정규화 로직을 따로 구현하지 않음 (학습/서빙 편향 방지).
    """
    mean: torch.Tensor   # register_buffer 로 등록되는 속성의 타입
    std: torch.Tensor
    def __init__(self, model: nn.Module, mean: Sequence[float], std: Sequence[float]):
        """
        Args:
            model: 정규화된 입력을 받는 내부 모델.
            mean: 특성별 평균 (학습 데이터에서 계산).
            std: 특성별 표준편차 (학습 데이터에서 계산).
        """
        super().__init__()
        self.model = model
        self.register_buffer("mean", torch.as_tensor(mean, dtype=torch.float32))
        self.register_buffer("std", torch.as_tensor(std, dtype=torch.float32))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """원래 단위의 입력을 정규화한 뒤 내부 모델에 넘긴다.
        Args:
            x: [N, 2] 입력 텐서 (배달 거리 km, 대기 주문 수 — 정규화 전 원래 값).
        Returns:
            [N, 1] 예상 시간(분) 텐서.
        """
        return self.model((x - self.mean) / self.std)

def build_model() -> WithNormalize:
    """학습과 서빙이 같은 구조를 쓰도록 한 곳에서 만든다.
    mean/std 는 자리만 잡아 두고, 실제 값은 학습 때 채우거나 저장된 state_dict 에서 불러온다.
    Returns:
        초기화된 WithNormalize(nn.Linear(2, 1)) 모델.
    """
    return WithNormalize(nn.Linear(2, 1), mean=[0.0, 0.0], std=[1.0, 1.0])

@cache
def load_model() -> WithNormalize:
    """처음 호출될 때 한 번만 모델 파일을 읽는다 (지연 로딩).
    학습 스크립트가 build_model 을 쓰려고 이 모듈을 import 할 때는 아직 모델 파일이 없기 때문이다.
    Returns:
        가중치를 불러와 eval 모드로 둔 모델.
    """
    model = build_model()
    path = os.path.join(os.getenv("MODEL_DIR", "artifacts"), "dl.pt")
    model.load_state_dict(torch.load(path))
    model.eval()
    return model

def estimate_delivery_minutes(distance_km: float, pending_orders: int) -> float:
    """학습된 PyTorch 모델로 배달 예상 시간(분)을 계산한다.
    Args:
        distance_km: 배달 거리(km, 정규화 전 원래 값).
        pending_orders: 대기 주문 수 (정규화 전 원래 값).
    Returns:
        예상 시간(분).
    """
    with torch.no_grad():
        x = torch.tensor([[distance_km, pending_orders]], dtype=torch.float32)
        return float(load_model()(x)[0, 0])
