import importlib.util
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import app

client = TestClient(app)
# 서버 내부 예외를 테스트 쪽으로 다시 던지지 않고 500 응답으로 받기 위한 클라이언트
lenient_client = TestClient(app, raise_server_exceptions=False)

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


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
        (0, 4, 18.0),  # 경계값: 거리만 0
        (4, 0, 22.0),  # 경계값: 주문만 0
        (0.001, 0, 10.003),  # 경계값: 아주 작은 양수 거리
        (50, 500, 1160.0),  # 경계값: 상한 (거리 50km, 주문 500건)
    ],
)
def test_eta_known_values(distance_km, pending_orders, expected):
    """기본(rule) 구현체에서 알려진 입력의 예상 시간을 돌려준다."""
    res = client.post(
        "/eta", json={"distance_km": distance_km, "pending_orders": pending_orders}
    )
    assert res.status_code == 200
    body = res.json()
    # 기대값은 rule 공식 기준이므로, 다른 구현체가 켜져 있으면 원인이 바로 보이게 먼저 확인한다
    assert body["model"] == "rule"
    assert body["minutes"] == pytest.approx(expected)


def test_health_default_model_is_rule():
    """MODEL_IMPL 을 지정하지 않은 기본 상태의 구현체는 rule 이다."""
    assert client.get("/health").json()["model"] == "rule"


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
        {"distance_km": None, "pending_orders": 1},  # null 거리
        {"distance_km": 1, "pending_orders": None},  # null 주문 수
        [1, 2],  # 객체가 아닌 배열
    ],
)
def test_eta_invalid_input_returns_422(payload):
    """잘못된 입력(음수, 숫자 아님, 정수 아님, 누락, null, 객체 아님)은 422 를 돌려준다."""
    res = client.post("/eta", json=payload)
    assert res.status_code == 422


def test_eta_malformed_json_returns_422():
    """JSON 으로 해석할 수 없는 본문은 422 를 돌려준다."""
    res = client.post(
        "/eta", content="abc", headers={"content-type": "application/json"}
    )
    assert res.status_code == 422


def test_eta_form_body_returns_422():
    """JSON 이 아닌 form 형식 본문은 422 를 돌려준다."""
    res = client.post("/eta", data={"distance_km": "1", "pending_orders": "1"})
    assert res.status_code == 422


@pytest.mark.parametrize(
    ("payload", "bad_field"),
    [
        ({"distance_km": -1, "pending_orders": 0}, "distance_km"),
        ({"distance_km": 1, "pending_orders": -1}, "pending_orders"),
        ({"distance_km": 1}, "pending_orders"),
    ],
)
def test_eta_422_detail_points_to_bad_field(payload, bad_field):
    """422 응답의 detail 은 문제가 된 필드 위치(loc)를 알려 준다."""
    res = client.post("/eta", json=payload)
    assert res.status_code == 422
    locs = [tuple(err["loc"]) for err in res.json()["detail"]]
    assert ("body", bad_field) in locs


# ── 현재 계약으로 확정한 관대한(lax) 입력 처리 ─────────────────────────────
# Pydantic 기본 동작을 그대로 계약으로 둔다. 엄격하게 바꾸기로 하면 이 테스트를 함께 고친다.
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"distance_km": "2.5", "pending_orders": "3"}, 23.5),  # 숫자 문자열
        ({"distance_km": 1, "pending_orders": 2.0}, 17.0),  # 소수부가 0인 실수 주문 수
        ({"distance_km": True, "pending_orders": False}, 13.0),  # bool → 1, 0
        ({"distance_km": -0.0, "pending_orders": 0}, 10.0),  # -0.0 은 0 으로 취급
        ({"distance_km": 1, "pending_orders": 1, "foo": 1}, 15.0),  # 모르는 필드는 무시
    ],
)
def test_eta_lenient_inputs_are_accepted(payload, expected):
    """숫자로 해석 가능한 입력과 추가 필드는 받아들이고 정상 값을 돌려준다."""
    res = client.post("/eta", json=payload)
    assert res.status_code == 200
    assert res.json()["minutes"] == pytest.approx(expected)


# ── 입력 범위: 상한(거리 50km, 주문 500건)과 비유한 수 ──────────────────────
# 500 이 아니라 422 가 나오는지 보려고, 서버 예외를 응답으로 받는 lenient_client 를 쓴다.
@pytest.mark.parametrize(
    ("payload", "bad_field"),
    [
        ({"distance_km": 50.001, "pending_orders": 0}, "distance_km"),  # 상한 바로 위
        ({"distance_km": 0, "pending_orders": 501}, "pending_orders"),  # 상한 바로 위
        ({"distance_km": 1e308, "pending_orders": 0}, "distance_km"),  # 예전 오버플로 입력
        ({"distance_km": 1, "pending_orders": 10**400}, "pending_orders"),  # 예전 오버플로 입력
    ],
)
def test_eta_above_upper_bound_returns_422(payload, bad_field):
    """상한을 넘는 입력은 서버 오류 없이 422 로 거부하고, 문제 필드를 알려 준다."""
    res = lenient_client.post("/eta", json=payload)
    assert res.status_code == 422
    locs = [tuple(err["loc"]) for err in res.json()["detail"]]
    assert ("body", bad_field) in locs


@pytest.mark.parametrize(
    "raw_body",
    [
        '{"distance_km": NaN, "pending_orders": 1}',
        '{"distance_km": Infinity, "pending_orders": 1}',
        '{"distance_km": -Infinity, "pending_orders": 1}',
        '{"distance_km": NaN}',  # 필드 누락 오류의 input(본문 전체)에 NaN 이 섞인 경우
    ],
)
def test_eta_non_finite_distance_returns_422(raw_body):
    """NaN, Infinity, -Infinity 거리는 서버 오류 없이 422 로 거부한다."""
    res = lenient_client.post(
        "/eta", content=raw_body, headers={"content-type": "application/json"}
    )
    assert res.status_code == 422
    assert ("body", "distance_km") in [tuple(e["loc"]) for e in res.json()["detail"]]


def test_eta_minutes_is_finite_at_upper_bound():
    """허용 범위의 최댓값에서도 minutes 는 유한한 숫자다."""
    res = client.post("/eta", json={"distance_km": 50, "pending_orders": 500})
    minutes = res.json()["minutes"]
    assert isinstance(minutes, (int, float)) and math.isfinite(minutes)


def test_unknown_model_impl_fails_at_import(monkeypatch):
    """MODEL_IMPL 에 없는 구현체 이름을 주면 앱을 불러오는 단계에서 바로 실패한다."""
    monkeypatch.setenv("MODEL_IMPL", "does_not_exist")
    # 이미 불러온 api.app 을 건드리지 않도록 별도 이름으로 새로 불러온다
    spec = importlib.util.spec_from_file_location("_app_bad_impl", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(ModuleNotFoundError):
        spec.loader.exec_module(module)


def test_eta_get_not_allowed():
    """/eta 는 POST 전용이라 GET 은 405 를 돌려준다."""
    assert client.get("/eta").status_code == 405
