import pytest

from api.predictors.rule import estimate_delivery_minutes


@pytest.mark.parametrize(
    ("distance_km", "pending_orders", "expected"),
    [
        (0, 0, 10.0),
        (1, 0, 13.0),
        (0, 1, 12.0),
        (2.5, 3, 23.5),
        (100, 50, 410.0),
    ],
)
def test_known_inputs_give_expected_minutes(distance_km, pending_orders, expected):
    """알려진 입력에 대해 기본 10분 + 1km당 3분 + 주문 1건당 2분 값을 돌려준다."""
    assert estimate_delivery_minutes(distance_km, pending_orders) == pytest.approx(expected)


def test_returns_float():
    """정수 입력이어도 float 을 돌려준다."""
    assert isinstance(estimate_delivery_minutes(1, 1), float)


@pytest.mark.parametrize(("distance_km", "pending_orders"), [(0, 0), (5, 5), (1000, 10000)])
def test_more_distance_or_orders_never_decreases_eta(distance_km, pending_orders):
    """0, 중간값, 큰 값 어디서든 거리나 대기 주문이 늘면 예상 시간이 줄지 않는다."""
    base = estimate_delivery_minutes(distance_km, pending_orders)
    assert estimate_delivery_minutes(distance_km + 1, pending_orders) > base
    assert estimate_delivery_minutes(distance_km, pending_orders + 1) > base
