"""PyTorch 학습: 배달 기록 수집 → 분할 → 정규화 통계 계산 → 학습 루프 → 평가 → 저장."""
import json
import numpy as np
import torch
from sklearn.model_selection import train_test_split
from training.data import collect_data
from api.predictors import rule
from api.predictors.dl import build_model

# 실험 추적 대상: 이 값에 따라 결과가 달라진다
HPARAMS = {"lr": 0.5, "epochs": 1000, "seed": 0}

torch.manual_seed(HPARAMS["seed"])
X, y = collect_data(seed=0)
X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=0)

model = build_model()
# 정규화 통계는 학습 데이터에서만 계산
model.mean.copy_(torch.tensor(np.mean(X_tr, axis=0)))
model.std.copy_(torch.tensor(np.std(X_tr, axis=0)))
Xt, yt = torch.tensor(X_tr), torch.tensor(y_tr).unsqueeze(1)
opt = torch.optim.Adam(model.parameters(), lr=HPARAMS["lr"])

for _ in range(HPARAMS["epochs"]):
    opt.zero_grad()
    loss = torch.nn.functional.mse_loss(model(Xt), yt)
    loss.backward()
    opt.step()

with torch.no_grad():
    pred = model(torch.tensor(X_te)).squeeze(1).numpy()

mae = float(np.abs(pred - y_te).mean())
baseline = float(np.abs(np.array([rule.estimate_delivery_minutes(a, b) for a, b in X_te]) - y_te).mean())
torch.save(model.state_dict(), "artifacts/dl.pt")

json.dump({"model": "dl", "mae": mae, "baseline_mae": baseline, "hparams": HPARAMS},
          open("artifacts/metrics_dl.json", "w"), indent=2)
print(f"dl 평균 오차 {mae:.2f}분  (기준선 rule {baseline:.2f}분)  hparams={HPARAMS}")
