import html
import importlib
import json
import math
import os
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

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
        ge=0, le=50, allow_inf_nan=False, description="가게에서 배달지까지 거리 (km)"
    )
    pending_orders: int = Field(ge=0, le=500, description="지금 밀려 있는 주문 수 (건)")

class EtaResponse(BaseModel):
    """배달 예상 시간 응답"""
    minutes: float = Field(description="배달 예상 시간 (분)")
    model: str = Field(description="예측을 낸 구현체 이름 (장애 추적용)")

APP_VERSION = "0.3.0"  # pyproject.toml 의 version 과 같아야 한다 (테스트로 확인)

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

# 소개 페이지 스타일. f-string 밖에 두어 CSS 중괄호를 두 번 쓰지 않아도 되게 한다.
_INDEX_CSS = """
:root { --bg:#f6f8fa; --fg:#1f2328; --muted:#59636e; --card:#fff; --line:#d1d9e0;
        --accent:#0969da; --btn:#0969da; --code:#eff2f5; --get:#1a7f37; --post:#9a6700; --err:#cf222e; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --card:#151b23; --line:#3d444d;
          --accent:#4493f8; --btn:#1158c7; --code:#212830; --get:#3fb950; --post:#d29922; --err:#f85149; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); line-height:1.6;
       font-family: system-ui, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif; }
main { max-width:760px; margin:0 auto; padding:40px 16px 64px; }
h1 { font-size:1.75rem; margin:0 0 4px; }
.lead { color:var(--muted); margin:0 0 12px; }
.badge { display:inline-block; font-size:.8rem; padding:2px 10px; margin-right:6px;
         border:1px solid var(--line); border-radius:999px; color:var(--muted); }
section { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:20px; margin-top:16px; }
h2 { font-size:1.05rem; margin:0 0 12px; }
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:.92rem; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; white-space:nowrap; }
tr:last-child td { border-bottom:none; }
code, pre { font-family: ui-monospace, Consolas, monospace; font-size:.88rem; }
code { background:var(--code); padding:1px 6px; border-radius:4px; }
pre { background:var(--code); padding:12px 14px; border-radius:8px; overflow-x:auto; margin:4px 0 12px; }
.method { font-family: ui-monospace, Consolas, monospace; font-weight:700; font-size:.82rem; }
.GET { color:var(--get); } .POST { color:var(--post); }
.label { color:var(--muted); font-size:.85rem; margin:0; }
.note { color:var(--muted); font-size:.88rem; margin:12px 0 0; }
a { color:var(--accent); }
form { display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; }
form label { display:flex; flex-direction:column; font-size:.85rem; color:var(--muted); gap:4px; }
input { font:inherit; color:var(--fg); background:var(--bg); border:1px solid var(--line);
        border-radius:6px; padding:6px 10px; width:150px; }
button { font:inherit; font-size:.9rem; cursor:pointer; border-radius:6px; padding:7px 16px;
         border:1px solid var(--btn); background:var(--btn); color:#fff; }  /* 흰 글자 대비 4.5:1 이상 */
button.secondary { background:transparent; color:var(--accent); padding:4px 12px; font-size:.82rem; }
button:disabled { opacity:.6; cursor:wait; }
.result { margin-top:14px; padding:12px 14px; border-radius:8px; background:var(--code); min-height:1.6em; }
.result strong { font-size:1.3rem; }
.result.error { color:var(--err); }
.copy-row { display:flex; justify-content:space-between; align-items:center; gap:8px; }
"""

# 페이지에서 바로 계산해 보기 + curl 복사. 외부 라이브러리 없이 같은 서버의 /eta 를 호출한다.
_INDEX_JS = """
const form = document.getElementById('try-form');
const result = document.getElementById('try-result');
const button = form.querySelector('button');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  button.disabled = true;  // 응답 전 중복 클릭 방지
  result.className = 'result';
  result.textContent = '계산 중…';
  const body = {
    distance_km: Number(form.distance_km.value),
    pending_orders: Number(form.pending_orders.value),
  };
  let res;
  try {
    res = await fetch('/eta', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (err) {
    // fetch 자체가 실패한 경우만 연결 실패로 본다
    result.className = 'result error';
    result.textContent = '서버에 연결하지 못했습니다.';
    button.disabled = false;
    return;
  }
  // 프록시가 HTML/텍스트 오류(502 등)를 돌려줄 수 있으므로 JSON 파싱 실패를 따로 처리한다
  let data = null;
  try { data = await res.json(); } catch (err) { data = null; }
  try {
    if (res.ok && data) {
      const strong = document.createElement('strong');
      strong.textContent = data.minutes + '분';
      result.replaceChildren('예상 배달 시간 ', strong, ' (구현체 ' + data.model + ')');
    } else {
      result.className = 'result error';
      const detail = data && Array.isArray(data.detail) ? data.detail : [];
      const msgs = detail.map((e) => (e.loc || []).slice(-1)[0] + ': ' + e.msg);
      result.textContent = res.status + ' 오류 - ' + (msgs.join(' / ') || '요청을 처리하지 못했습니다.');
    }
  } finally {
    button.disabled = false;
  }
});

// curl 예시의 주소를 지금 접속한 서버 주소로 바꾼다
const curl = document.getElementById('curl');
curl.textContent = curl.textContent.replace('https://<서버주소>', location.origin);
const copyBtn = document.getElementById('copy-curl');
copyBtn.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(curl.textContent);
    copyBtn.textContent = '복사됨';
  } catch (err) {
    copyBtn.textContent = '복사 실패 - 직접 선택해 주세요';
  }
  setTimeout(() => { copyBtn.textContent = '복사'; }, 1500);
});
"""

_TYPE_LABEL = {"number": "실수", "integer": "정수", "string": "문자열"}


def _field_rows(model: type[BaseModel], with_range: bool) -> str:
    """Pydantic 모델의 스키마에서 필드 표의 행을 만든다 (검증 규칙과 페이지가 어긋나지 않게)."""
    rows = []
    for name, prop in model.model_json_schema()["properties"].items():
        cells = [f"<code>{name}</code>", _TYPE_LABEL.get(prop.get("type"), prop.get("type", ""))]
        if with_range:
            cells.append(f"{prop.get('minimum', '')} ~ {prop.get('maximum', '')}")
        cells.append(html.escape(prop.get("description", "")))
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return "\n".join(rows)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """API 소개 페이지 (HTML)"""
    # 예시는 실제 계산 함수로 만든다 → 구현이 바뀌어도 페이지 예시가 틀리지 않는다
    example_req = {"distance_km": 2.5, "pending_orders": 3}
    example_res = eta(EtaRequest(**example_req)).model_dump()
    example_req_json = json.dumps(example_req, ensure_ascii=False)
    # 폼의 입력 범위도 검증 규칙(스키마)에서 가져온다
    props = EtaRequest.model_json_schema()["properties"]
    dist, orders = props["distance_km"], props["pending_orders"]
    # 422 예시는 실제 검증 오류로 만든다 (상한을 넘는 거리). FastAPI 는 loc 앞에 "body" 를 붙인다.
    bad_req = {"distance_km": dist["maximum"] + 1, "pending_orders": 3}
    try:
        EtaRequest(**bad_req)
        example_422 = None
    except ValidationError as e:
        err = e.errors(include_url=False)[0]
        example_422 = {"detail": [{"type": err["type"], "loc": ["body", *err["loc"]], "msg": err["msg"]}]}
    formula = (
        '<p class="note">계산 방식(rule): 기본 10분 + 1km당 3분 + 대기 주문 1건당 2분</p>'
        if MODEL_IMPL == "rule" else ""
    )
    return f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>배달 예상 시간 API</title>
<style>{_INDEX_CSS}</style>
</head>
<body><main>
  <h1>배달 예상 시간 API</h1>
  <p class="lead">가게에서 배달지까지 거리와 밀려 있는 주문 수로 배달 예상 시간(분)을 계산합니다.</p>
  <span class="badge">버전 {APP_VERSION}</span><span class="badge">구현체 {MODEL_IMPL}</span>

  <section>
    <h2>바로 계산해 보기</h2>
    <form id="try-form">
      <label for="distance_km">거리 (km, {dist["minimum"]}~{dist["maximum"]})
        <input id="distance_km" name="distance_km" type="number" step="any" required
               min="{dist["minimum"]}" max="{dist["maximum"]}" value="{example_req["distance_km"]}"></label>
      <label for="pending_orders">대기 주문 (건, {orders["minimum"]}~{orders["maximum"]})
        <input id="pending_orders" name="pending_orders" type="number" step="1" required
               min="{orders["minimum"]}" max="{orders["maximum"]}" value="{example_req["pending_orders"]}"></label>
      <button type="submit">계산</button>
    </form>
    <div id="try-result" class="result" role="status" aria-live="polite">값을 입력하고 계산을 누르세요.</div>
  </section>

  <section>
    <h2>엔드포인트</h2>
    <div class="table-wrap"><table>
      <tr><th>메서드</th><th>경로</th><th>설명</th></tr>
      <tr><td class="method GET">GET</td><td><code>/</code></td><td>소개 페이지 (지금 이 화면)</td></tr>
      <tr><td class="method POST">POST</td><td><code>/eta</code></td><td>배달 예상 시간 계산</td></tr>
      <tr><td class="method GET">GET</td><td><a href="/health"><code>/health</code></a></td><td>서비스 상태, 구현체, 버전 확인</td></tr>
      <tr><td class="method GET">GET</td><td><a href="/docs"><code>/docs</code></a></td><td>브라우저에서 직접 호출해 볼 수 있는 API 문서</td></tr>
    </table></div>
  </section>

  <section>
    <h2>요청 <code>POST /eta</code></h2>
    <div class="table-wrap"><table>
      <tr><th>필드</th><th>타입</th><th>허용 범위</th><th>설명</th></tr>
      {_field_rows(EtaRequest, with_range=True)}
    </table></div>
    <p class="note">JSON 본문으로 보내며, 두 필드 모두 필수입니다.</p>
  </section>

  <section>
    <h2>응답</h2>
    <div class="table-wrap"><table>
      <tr><th>필드</th><th>타입</th><th>설명</th></tr>
      {_field_rows(EtaResponse, with_range=False)}
    </table></div>
    {formula}
  </section>

  <section>
    <h2>예시</h2>
    <p class="label">요청</p>
    <pre id="example-req">{example_req_json}</pre>
    <p class="label">응답 (200)</p>
    <pre id="example-res">{json.dumps(example_res, ensure_ascii=False)}</pre>
    <div class="copy-row">
      <p class="label">터미널에서 호출 (macOS·Linux·Git Bash)</p>
      <button type="button" class="secondary" id="copy-curl">복사</button>
    </div>
    <pre id="curl">curl -X POST https://&lt;서버주소&gt;/eta -H "Content-Type: application/json" -d '{html.escape(example_req_json)}'</pre>
  </section>

  <section>
    <h2>오류 응답</h2>
    <div class="table-wrap"><table>
      <tr><th>상황</th><th>상태 코드</th></tr>
      <tr><td>허용 범위를 벗어난 값 (음수 포함)</td><td><code>422</code></td></tr>
      <tr><td>숫자가 아닌 값, <code>null</code>, NaN·Infinity</td><td><code>422</code></td></tr>
      <tr><td>필드 누락, JSON이 아닌 본문</td><td><code>422</code></td></tr>
      <tr><td><code>/eta</code>를 GET으로 호출</td><td><code>405</code></td></tr>
    </table></div>
    <p class="note">422 응답의 <code>detail[].loc</code>는 문제가 된 필드를, <code>msg</code>는 이유를 알려 줍니다.
      숫자 문자열(<code>"2.5"</code>)은 숫자로 받아들이고, 정의되지 않은 필드는 무시합니다.</p>
    <p class="label" style="margin-top:12px">예: 거리 {bad_req["distance_km"]}km 요청 → 422 (일부 필드 생략)</p>
    <pre id="example-422">{html.escape(json.dumps(example_422, ensure_ascii=False))}</pre>
  </section>
</main>
<script>{_INDEX_JS}</script>
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

