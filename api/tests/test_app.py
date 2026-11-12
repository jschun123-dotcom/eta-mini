import pytest
from fastapi.testclient import TestClient

from api.app import app

client = TestClient(app)


def test_health_returns_ok_and_model_name():
    """/health 는 200 과 status=ok, 구현체 이름(model)을 돌려준다."""
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert isinstance(body["model"], str) and body["model"]


def test_eta_response_shape():
    """/eta 정상 요청은 200 과 minutes(숫자), model(문자열) 필드만 돌려준다."""
    res = client.post("/eta", json={"distance_km": 2.5, "pending_orders": 3})
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"minutes", "model"}
    assert isinstance(body["minutes"], (int, float))
    assert isinstance(body["model"], str)


def test_eta_model_matches_health():
    """/eta 응답의 model 은 /health 가 알려 주는 구현체 이름과 같다."""
    health_model = client.get("/health").json()["model"]
    res = client.post("/eta", json={"distance_km": 1, "pending_orders": 1})
    assert res.json()["model"] == health_model


@pytest.mark.parametrize(
    ("distance_km", "pending_orders", "expected"),
    [
        (2.5, 3, 23.5),  # 정상 입력
        (0, 0, 10.0),  # 경계값: 0
        (1000, 10000, 23010.0),  # 경계값: 큰 값
    ],
)
def test_eta_known_values(distance_km, pending_orders, expected):
    """기본(rule) 구현체에서 알려진 입력의 예상 시간을 돌려준다."""
    res = client.post(
        "/eta", json={"distance_km": distance_km, "pending_orders": pending_orders}
    )
    assert res.status_code == 200
    assert res.json()["minutes"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "payload",
    [
        {"distance_km": -0.1, "pending_orders": 0},  # 음수 거리
        {"distance_km": 1, "pending_orders": -1},  # 음수 주문 수
        {"distance_km": "far", "pending_orders": 1},  # 숫자가 아닌 거리
        {"distance_km": 1, "pending_orders": "many"},  # 숫자가 아닌 주문 수
        {"distance_km": 1, "pending_orders": 1.5},  # 정수가 아닌 주문 수
        {"distance_km": 1},  # 필드 누락
        {"pending_orders": 1},  # 필드 누락
        {},  # 빈 본문
    ],
)
def test_eta_invalid_input_returns_422(payload):
    """잘못된 입력(음수, 숫자 아님, 정수 아님, 누락)은 422 를 돌려준다."""
    res = client.post("/eta", json=payload)
    assert res.status_code == 422


def test_eta_get_not_allowed():
    """/eta 는 POST 전용이라 GET 은 405 를 돌려준다."""
    assert client.get("/eta").status_code == 405
