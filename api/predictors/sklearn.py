import os
import joblib

_model = joblib.load(os.path.join(os.getenv("MODEL_DIR", "artifacts"), "sklearn.joblib"))


def estimate_delivery_minutes(distance_km: float, pending_orders: int) -> float:
    """학습된 scikit-learn 파이프라인으로 배달 예상 시간(분)을 계산한다.

    파이프라인 안에 StandardScaler 가 들어 있으므로 원래 단위의 값을 그대로 넣는다.

    Args:
        distance_km: 배달 거리(km).
        pending_orders: 대기 주문 수.

    Returns:
        예상 시간(분).
    """
    return float(_model.predict([[distance_km, pending_orders]])[0])
