import os
import numpy as np
import onnxruntime as ort

_session = ort.InferenceSession(os.path.join(os.getenv("MODEL_DIR", "artifacts"), "model_sklearn.onnx"))

def estimate_delivery_minutes(distance_km: float, pending_orders: int) -> float:
    """ONNX 로 변환한 모델로 배달 예상 시간(분)을 계산한다.
    scikit-learn 없이 ONNX Runtime 만으로 실행한다. 정규화는 모델 안에 있다.
    Args:
        distance_km: 배달 거리(km).
        pending_orders: 대기 주문 수.
    Returns:
        예상 시간(분).
    """
    x = np.array([[distance_km, pending_orders]], dtype=np.float32)
    return float(_session.run(["variable"], {"X": x})[0][0, 0])
