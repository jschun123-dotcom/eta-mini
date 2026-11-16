"""Python API 와 다른 언어 서비스(Go, Rust)의 예측이 같은지 비교한다 (parity).

기준은 학습한 원본 모델(MODEL_IMPL=sklearn)로 띄운 Python API 다.
ONNX 변환과 다른 언어 서빙을 거친 뒤에도 같은 입력에 같은 값을 내는지 확인한다.
하나라도 FAIL 이면 exit 1 → CI 가 멈춰 이미지가 만들어지지 않는다.

pytest 테스트가 아니다. 서버가 떠 있어야 하므로 직접 실행한다.
    uv run python -m training.check_parity

환경 변수
    PY_URL   기준 Python API (기본 http://127.0.0.1:8080)
    TARGETS  비교 대상 "이름=URL" 목록, 쉼표로 구분
             (기본 go=http://127.0.0.1:8081,rust=http://127.0.0.1:8082)
"""
import json
import os
import random
import sys
import urllib.request

TOLERANCE = 1e-4  # 분. 최대 오차가 이보다 작아야 PASS
TIMEOUT = 10  # 초. 요청 하나당

PY_URL = os.getenv("PY_URL", "http://127.0.0.1:8080")
TARGETS = os.getenv("TARGETS", "go=http://127.0.0.1:8081,rust=http://127.0.0.1:8082")


def parse_targets(spec: str) -> dict[str, str]:
    """ "go=http://...,rust=http://..." 를 {"go": "http://...", ...} 로 바꾼다."""
    targets = {}
    for item in spec.split(","):
        if item.strip():
            name, url = item.split("=", 1)
            targets[name.strip()] = url.strip()
    return targets


def make_inputs() -> list[tuple[float, int]]:
    """고정 입력 3건 + seed 0 으로 뽑은 무작위 20건. 같은 seed 면 항상 같은 입력이다."""
    rng = random.Random(0)
    randoms = [(round(rng.uniform(0.5, 8.0), 3), rng.randint(0, 10)) for _ in range(20)]
    return [(3, 2), (0, 0), (8, 10)] + randoms


def predict(base_url: str, distance_km: float, pending_orders: int) -> float:
    """POST /eta 를 호출해 예측 시간(분)을 돌려준다."""
    body = json.dumps({"distance_km": distance_km, "pending_orders": pending_orders}).encode()
    req = urllib.request.Request(f"{base_url.rstrip('/')}/eta", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        return float(json.load(res)["minutes"])


def main() -> int:
    inputs = make_inputs()

    try:
        expected = [predict(PY_URL, d, o) for d, o in inputs]
    except Exception as e:  # 기준이 없으면 비교 자체가 불가능하다
        print(f"FAIL [python] {PY_URL} 호출 실패: {e}")
        return 1

    ok = True
    for name, url in parse_targets(TARGETS).items():
        try:
            got = [predict(url, d, o) for d, o in inputs]
        except Exception as e:
            print(f"FAIL [{name}] {url} 호출 실패: {e}")
            ok = False
            continue

        diffs = [abs(g - w) for g, w in zip(got, expected)]
        i = max(range(len(diffs)), key=diffs.__getitem__)
        passed = diffs[i] < TOLERANCE
        ok &= passed
        d, o = inputs[i]
        print(f"{'PASS' if passed else 'FAIL'} [{name}] {len(inputs)}건 최대 오차 {diffs[i]:.2e}분 "
              f"(기준 < {TOLERANCE}) — 최대 오차 입력 ({d}km, {o}건): "
              f"python {expected[i]:.6f} vs {name} {got[i]:.6f}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
