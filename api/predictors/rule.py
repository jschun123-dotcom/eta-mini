def estimate_delivery_minutes(distance_km: float, pending_orders: int) -> float:
    """배달 예상 시간(분)을 계산

    모델 없이 배포하는 첫 구현체이자, 이후 모델들이 넘어야 할 기준선(baseline)이다.

    Args:
        distance_km: 가게에서 배달지까지 거리(km).
        pending_orders: 지금 밀려 있는 주문 수.

    Returns:
        예상 시간(분) = 기본 10분 + 1km당 3분 + 대기 주문 1건당 2분.
    """
    return 10.0 + 3.0 * distance_km + 2.0 * pending_orders

