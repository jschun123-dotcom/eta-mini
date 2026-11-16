package main

// 요청 본문 해석과 검증. api/app.py 의 EtaRequest(Pydantic lax 모드)와 같은 규칙을 따른다.
// 기준은 api/tests/test_app.py 에 적힌 계약이다.
//
//   - 숫자 문자열("2.5"), 소수부가 0인 실수 주문 수(2.0), bool(→1/0)은 받아들인다
//   - NaN·Infinity 는 422 (Python json 은 NaN/Infinity 토큰을 읽으므로 여기서도 읽은 뒤 거부한다)
//   - 범위(거리 0~50, 주문 0~500)를 벗어나면 422, 모르는 필드는 무시한다
//   - 422 본문은 FastAPI 와 같은 {"detail": [{"type", "loc", "msg", "input", "ctx"?}]} 형식이다

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math"
	"math/big"
	"mime"
	"regexp"
	"strconv"
	"strings"
)

// valErr 는 FastAPI 422 응답의 detail 항목 하나다.
type valErr struct {
	Type  string         `json:"type"`
	Loc   []any          `json:"loc"`
	Msg   string         `json:"msg"`
	Input any            `json:"input"`
	Ctx   map[string]any `json:"ctx,omitempty"`
}

// nonFinite 는 JSON 본문의 NaN/Infinity/-Infinity 토큰을 읽은 값이다 (encoding/json 은 이 토큰을 모른다).
type nonFinite string

// MarshalJSON 은 FastAPI 의 _json_safe 처럼 비유한 수를 문자열로 내보낸다.
func (n nonFinite) MarshalJSON() ([]byte, error) { return json.Marshal(string(n)) }

func (n nonFinite) float() float64 {
	switch n {
	case "NaN":
		return math.NaN()
	case "-Infinity":
		return math.Inf(-1)
	}
	return math.Inf(1)
}

// 전처리로 바꿔 넣는 표식. 실제 요청 문자열과 겹치지 않도록 NUL 문자로 시작한다.
const nonFinitePrefix = "\x00nonfinite:"

type etaInput struct {
	DistanceKm    float64
	PendingOrders int64
}

// parseBody 는 FastAPI 처럼 Content-Type 에 따라 본문을 JSON 으로 읽는다.
// JSON 으로 읽지 않는 경우(form 등)는 본문 문자열 자체를 값으로 돌려줘, 이후 검증에서 422 가 난다.
func parseBody(contentType string, body []byte) (any, *valErr) {
	isJSON := contentType == ""
	if !isJSON {
		if mt, _, err := mime.ParseMediaType(contentType); err == nil {
			isJSON = mt == "application/json" || (strings.HasPrefix(mt, "application/") && strings.HasSuffix(mt, "+json"))
		}
	}
	if len(body) == 0 {
		return nil, nil // 본문 없음 → 이후 "missing"
	}
	if !isJSON {
		return string(body), nil
	}

	dec := json.NewDecoder(bytes.NewReader(replaceNonFiniteTokens(body)))
	dec.UseNumber() // 큰 정수(10**400 등)를 잃지 않도록 숫자를 문자열 그대로 받는다
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, jsonInvalid(err)
	}
	if _, err := dec.Token(); err != io.EOF { // 값 뒤에 다른 내용이 붙어 있으면 거부 (Python json.loads 와 같다)
		// 위치는 첫 값 뒤 공백을 건너뛴 곳 (NaN 토큰을 바꿔 넣은 본문에서는 조금 어긋날 수 있다)
		pos := dec.InputOffset()
		for pos < int64(len(body)) && strings.ContainsRune(" \t\r\n", rune(body[pos])) {
			pos++
		}
		return nil, &valErr{Type: "json_invalid", Loc: []any{"body", pos}, Msg: "JSON decode error",
			Input: map[string]any{}, Ctx: map[string]any{"error": "Extra data"}}
	}
	return restoreNonFinite(v), nil
}

func jsonInvalid(err error) *valErr {
	pos := int64(0)
	var se *json.SyntaxError
	if errors.As(err, &se) && se.Offset > 0 {
		pos = se.Offset - 1 // Go 는 문제 문자를 읽은 "뒤"의 위치, Python 은 문제 문자의 위치를 준다
	}
	return &valErr{Type: "json_invalid", Loc: []any{"body", pos}, Msg: "JSON decode error",
		Input: map[string]any{}, Ctx: map[string]any{"error": err.Error()}}
}

// replaceNonFiniteTokens 는 문자열 바깥의 NaN, Infinity, -Infinity 토큰을 표식 문자열로 바꾼다.
func replaceNonFiniteTokens(b []byte) []byte {
	var out bytes.Buffer
	inStr, esc := false, false
	for i := 0; i < len(b); i++ {
		c := b[i]
		if inStr {
			out.WriteByte(c)
			switch {
			case esc:
				esc = false
			case c == '\\':
				esc = true
			case c == '"':
				inStr = false
			}
			continue
		}
		if c == '"' {
			inStr = true
			out.WriteByte(c)
			continue
		}
		replaced := false
		for _, tok := range []string{"NaN", "Infinity", "-Infinity"} {
			if bytes.HasPrefix(b[i:], []byte(tok)) {
				out.WriteString(`"\u0000nonfinite:` + tok + `"`)
				i += len(tok) - 1
				replaced = true
				break
			}
		}
		if !replaced {
			out.WriteByte(c)
		}
	}
	return out.Bytes()
}

func restoreNonFinite(v any) any {
	switch x := v.(type) {
	case string:
		if tok, ok := strings.CutPrefix(x, nonFinitePrefix); ok {
			return nonFinite(tok)
		}
	case map[string]any:
		for k, e := range x {
			x[k] = restoreNonFinite(e)
		}
	case []any:
		for i, e := range x {
			x[i] = restoreNonFinite(e)
		}
	}
	return v
}

// validateEta 는 파싱한 본문을 EtaRequest 규칙으로 검증한다. 오류는 필드 순서대로 모두 모은다.
func validateEta(body any) (etaInput, []valErr) {
	obj, ok := body.(map[string]any)
	if body == nil {
		return etaInput{}, []valErr{{Type: "missing", Loc: []any{"body"}, Msg: "Field required", Input: nil}}
	}
	if !ok {
		return etaInput{}, []valErr{{Type: "model_attributes_type", Loc: []any{"body"},
			Msg: "Input should be a valid dictionary or object to extract fields from", Input: body}}
	}

	var in etaInput
	var errs []valErr
	if v, ok := obj["distance_km"]; !ok {
		errs = append(errs, missing("distance_km", obj))
	} else if f, e := checkFloat(v, 0, 50); e != nil {
		errs = append(errs, at("distance_km", v, e))
	} else {
		in.DistanceKm = f
	}
	if v, ok := obj["pending_orders"]; !ok {
		errs = append(errs, missing("pending_orders", obj))
	} else if n, e := checkInt(v, 0, 500); e != nil {
		errs = append(errs, at("pending_orders", v, e))
	} else {
		in.PendingOrders = n
	}
	return in, errs
}

func missing(field string, obj map[string]any) valErr {
	return valErr{Type: "missing", Loc: []any{"body", field}, Msg: "Field required", Input: obj}
}

func at(field string, input any, e *valErr) valErr {
	e.Loc = []any{"body", field}
	e.Input = input
	return *e
}

var (
	errFloatType    = valErr{Type: "float_type", Msg: "Input should be a valid number"}
	errFloatParsing = valErr{Type: "float_parsing", Msg: "Input should be a valid number, unable to parse string as a number"}
	errIntType      = valErr{Type: "int_type", Msg: "Input should be a valid integer"}
	errIntParsing   = valErr{Type: "int_parsing", Msg: "Input should be a valid integer, unable to parse string as an integer"}
	errIntFromFloat = valErr{Type: "int_from_float", Msg: "Input should be a valid integer, got a number with a fractional part"}
	errFinite       = valErr{Type: "finite_number", Msg: "Input should be a finite number"}
)

func fail(e valErr) *valErr { return &e }

func rangeErr(below bool, bound int) *valErr {
	if below {
		return &valErr{Type: "greater_than_equal", Msg: "Input should be greater than or equal to " + strconv.Itoa(bound),
			Ctx: map[string]any{"ge": bound}}
	}
	return &valErr{Type: "less_than_equal", Msg: "Input should be less than or equal to " + strconv.Itoa(bound),
		Ctx: map[string]any{"le": bound}}
}

// checkFloat: float 필드 (allow_inf_nan=False, ge, le).
func checkFloat(v any, ge, le int) (float64, *valErr) {
	var f float64
	switch x := v.(type) {
	case bool:
		f = boolNum(x)
	case nonFinite:
		f = x.float()
	case json.Number:
		var err error
		if f, err = strconv.ParseFloat(string(x), 64); err != nil && !errors.Is(err, strconv.ErrRange) {
			return 0, fail(errFloatParsing) // 범위 초과는 ±Inf 로 받아 아래에서 finite_number 가 된다
		}
	case string:
		var ok bool
		if f, ok = parseFloatString(x); !ok {
			return 0, fail(errFloatParsing)
		}
	default: // null, 배열, 객체
		return 0, fail(errFloatType)
	}
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return 0, fail(errFinite)
	}
	if f < float64(ge) {
		return 0, rangeErr(true, ge)
	}
	if f > float64(le) {
		return 0, rangeErr(false, le)
	}
	return f, nil
}

// checkInt: int 필드 (ge, le). 큰 정수는 big.Int 로 비교해 오버플로 없이 422 를 낸다.
func checkInt(v any, ge, le int) (int64, *valErr) {
	var n *big.Int
	switch x := v.(type) {
	case bool:
		n = big.NewInt(int64(boolNum(x)))
	case nonFinite:
		return 0, fail(errFinite)
	case json.Number:
		var e *valErr
		if n, e = intFromNumber(string(x)); e != nil {
			return 0, e
		}
	case string:
		var ok bool
		if n, ok = parseIntString(x); !ok {
			return 0, fail(errIntParsing)
		}
	default:
		return 0, fail(errIntType)
	}
	if n.Cmp(big.NewInt(int64(ge))) < 0 {
		return 0, rangeErr(true, ge)
	}
	if n.Cmp(big.NewInt(int64(le))) > 0 {
		return 0, rangeErr(false, le)
	}
	return n.Int64(), nil
}

func boolNum(b bool) float64 {
	if b {
		return 1
	}
	return 0
}

// intFromNumber: JSON 숫자 → 정수. 정수 표기면 그대로, 실수 표기면 소수부가 0일 때만 받는다.
func intFromNumber(s string) (*big.Int, *valErr) {
	if !strings.ContainsAny(s, ".eE") {
		n, ok := new(big.Int).SetString(s, 10)
		if !ok {
			return nil, fail(errIntParsing)
		}
		return n, nil
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil && !errors.Is(err, strconv.ErrRange) {
		return nil, fail(errIntParsing)
	}
	if math.IsInf(f, 0) {
		return nil, fail(errFinite)
	}
	if f != math.Trunc(f) {
		return nil, fail(errIntFromFloat)
	}
	n, _ := big.NewFloat(f).Int(nil)
	return n, nil
}

// 밑줄은 숫자 사이에 하나씩만 올 수 있다 (Python/Pydantic 과 같다: "1_000" 가능, "_1", "1__0", "1_" 불가).
var badUnderscore = regexp.MustCompile(`(^|[^0-9])_|_([^0-9]|$)`)

func stripUnderscores(s string) (string, bool) {
	if badUnderscore.MatchString(s) {
		return "", false
	}
	return strings.ReplaceAll(s, "_", ""), true
}

// parseFloatString: 숫자 문자열 → 실수. "inf"/"nan" 은 읽은 뒤 finite 검사에서 거부된다.
// Pydantic 은 앞뒤 공백과 밑줄을 각각은 받지만 둘이 함께 있으면(" 1_0 ") 거부한다 (정수는 둘 다 받는다).
func parseFloatString(s string) (float64, bool) {
	trimmed := strings.TrimSpace(s)
	if trimmed != s && strings.Contains(s, "_") {
		return 0, false
	}
	s, ok := stripUnderscores(trimmed)
	if !ok || strings.ContainsAny(s, "xXpP") { // Go 만 받는 16진 실수 표기(0x1p3)는 거부
		return 0, false
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil && !errors.Is(err, strconv.ErrRange) {
		return 0, false
	}
	return f, true
}

// 정수 문자열: 부호 + 숫자, 그리고 "3.0" 처럼 0 으로만 된 소수부는 허용한다 ("3.5", "1e1" 은 불가).
var intString = regexp.MustCompile(`^[+-]?[0-9]+(\.0*)?$`)

func parseIntString(s string) (*big.Int, bool) {
	s, ok := stripUnderscores(strings.TrimSpace(s))
	if !ok || !intString.MatchString(s) {
		return nil, false
	}
	s, _, _ = strings.Cut(s, ".")
	return new(big.Int).SetString(s, 10)
}
