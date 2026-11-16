// delivery-eta 의 Go 구현. api/app.py 와 같은 계약(POST /eta, GET /health)을 따른다.
// 예측은 ONNX 모델(입력 "X" [1, 2] float32 → 출력 "variable" [1, 1])을 ONNX Runtime 으로 실행한다.
// 정규화는 모델 안에 있으므로 원래 단위의 값을 그대로 넣는다.
//
// 환경 변수
//
//	PORT       듣는 포트 (기본 8080)
//	MODEL_DIR  model_sklearn.onnx 가 있는 폴더 (기본 artifacts, Python 구현과 같다)
//	ORT_LIB    libonnxruntime.so 경로 (기본 /usr/local/lib/libonnxruntime.so)
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"

	ort "github.com/yalue/onnxruntime_go"
)

// version 은 빌드할 때 pyproject.toml 의 version 으로 채운다 (-ldflags "-X main.version=...").
var version = "dev"

// modelName 은 /health 와 /eta 응답의 model. 장애 추적 때 Python 구현과 구분되도록 이름을 따로 둔다.
const modelName = "onnx-go"

const maxBodyBytes = 1 << 20

type predictor struct {
	session *ort.DynamicAdvancedSession
}

func newPredictor(libPath, modelPath string) (*predictor, error) {
	ort.SetSharedLibraryPath(libPath)
	if err := ort.InitializeEnvironment(); err != nil {
		return nil, fmt.Errorf("ONNX Runtime 초기화 실패 (%s): %w", libPath, err)
	}
	s, err := ort.NewDynamicAdvancedSession(modelPath, []string{"X"}, []string{"variable"}, nil)
	if err != nil {
		return nil, fmt.Errorf("모델 불러오기 실패 (%s): %w", modelPath, err)
	}
	return &predictor{session: s}, nil
}

// predict 는 요청마다 새 텐서를 만들어 실행한다. ORT 세션의 Run 은 동시에 불러도 안전하다.
func (p *predictor) predict(distanceKm float64, pendingOrders int64) (float64, error) {
	in, err := ort.NewTensor(ort.NewShape(1, 2), []float32{float32(distanceKm), float32(pendingOrders)})
	if err != nil {
		return 0, err
	}
	defer in.Destroy()
	out, err := ort.NewEmptyTensor[float32](ort.NewShape(1, 1))
	if err != nil {
		return 0, err
	}
	defer out.Destroy()
	if err := p.session.Run([]ort.Value{in}, []ort.Value{out}); err != nil {
		return 0, err
	}
	return float64(out.GetData()[0]), nil
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("응답 쓰기 실패: %v", err)
	}
}

// onlyMethod 는 FastAPI 처럼 다른 메서드에 405 와 {"detail": "Method Not Allowed"} 를 돌려준다.
func onlyMethod(method string, h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != method {
			w.Header().Set("Allow", method)
			writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"detail": "Method Not Allowed"})
			return
		}
		h(w, r)
	}
}

func newMux(p *predictor) *http.ServeMux {
	mux := http.NewServeMux()

	mux.HandleFunc("/health", onlyMethod(http.MethodGet, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "model": modelName, "version": version})
	}))

	mux.HandleFunc("/eta", onlyMethod(http.MethodPost, func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBodyBytes))
		if err != nil {
			var tooLarge *http.MaxBytesError
			if errors.As(err, &tooLarge) {
				writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{"detail": "Request Entity Too Large"})
				return
			}
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "본문을 읽지 못했습니다"})
			return
		}
		parsed, jerr := parseBody(r.Header.Get("Content-Type"), body)
		if jerr != nil {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]any{"detail": []valErr{*jerr}})
			return
		}
		in, verrs := validateEta(parsed)
		if len(verrs) > 0 {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]any{"detail": verrs})
			return
		}
		minutes, err := p.predict(in.DistanceKm, in.PendingOrders)
		if err != nil {
			log.Printf("예측 실패: %v", err)
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "Internal Server Error"})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"minutes": minutes, "model": modelName})
	}))

	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Not Found"})
	})
	return mux
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func main() {
	modelPath := filepath.Join(getenv("MODEL_DIR", "artifacts"), "model_sklearn.onnx")
	p, err := newPredictor(getenv("ORT_LIB", "/usr/local/lib/libonnxruntime.so"), modelPath)
	if err != nil {
		log.Fatal(err)
	}
	addr := ":" + getenv("PORT", "8080")
	log.Printf("delivery-eta (go) %s, model=%s, listening on %s", version, modelPath, addr)
	log.Fatal(http.ListenAndServe(addr, newMux(p)))
}
