"""학습된 두 모델을 ONNX 로 내보내고, 원본과 예측이 같은지 확인한다.
두 모델 모두 입력 "X" [N, 2] (배달 거리 km, 대기 주문 수) → 출력 "variable" [N, 1] (분) 으로 이름을 맞춘다.
이 이름이 서빙 쪽(Python onnx 구현체, Go 서비스)과의 계약이다.
    uv run --group train python -m training.export_onnx
"""

import logging
import sys
import joblib
import numpy as np
import onnxruntime as ort
import torch
from onnx import ModelProto
from skl2onnx import to_onnx
from api.predictors.dl import build_model

# scikit-learn → ONNX (make_pipeline 이라 정규화 포함)
sk_model = joblib.load("artifacts/sklearn.joblib")
probe = np.array([[3, 2], [0, 0], [7.5, 10], [1.2, 4]], dtype=np.float32)
onx = to_onnx(sk_model, probe[:1])                     # sklearn: 정규화 포함
assert isinstance(onx, ModelProto)                     # 변환 결과가 ONNX 모델인지 확인

with open("artifacts/model_sklearn.onnx", "wb") as f:
    f.write(onx.SerializeToString())

# 새 변환기가 내는 "torchvision is not installed" 경고는 이 모델과 무관하므로 숨긴다
logging.getLogger("torch.onnx").setLevel(logging.ERROR)

# PyTorch → ONNX (WithNormalize 라 정규화 포함)
dl_model = build_model()
dl_model.load_state_dict(torch.load("artifacts/dl.pt"))
dl_model.eval()
# PyTorch 2.9부터 기본이 된 `torch.export` 기반 변환기를 쓴다. 이 변환기가 `onnxscript`를 쓴다.
torch.onnx.export(dl_model, (torch.zeros(1, 2),), "artifacts/model_dl.onnx",
                  input_names=["X"], output_names=["variable"],
                  dynamic_shapes=({0: torch.export.Dim("batch")},), external_data=False, verbose=False)

# 검증: 원본 vs ONNX
with torch.no_grad():
    originals = {"model_sklearn.onnx": sk_model.predict(probe),
                 "model_dl.onnx": dl_model(torch.tensor(probe)).squeeze(1).numpy()}
ok = True
for fname, want in originals.items():
    sess = ort.InferenceSession(f"artifacts/{fname}")
    got = np.asarray(sess.run(["variable"], {"X": probe})[0]).ravel()
    diff = float(np.abs(got - want).max())
    ok &= diff < 1e-4
    print(f"{fname}: 원본 vs ONNX 최대 오차 {diff:.2e}", "OK" if diff < 1e-4 else "FAIL")

sys.exit(0 if ok else 1)
