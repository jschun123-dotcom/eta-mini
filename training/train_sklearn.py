"""scikit-learn 학습: 배달 기록 수집 → 분할 → 정규화+학습 → 평가 → 저장.

같은 seed 로 다시 돌리면 같은 모델이 나온다 (재현성).
"""
import json
import joblib
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from training.data import collect_data
from api.predictors import rule

X, y = collect_data(seed=0)
X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=0)

model = make_pipeline(StandardScaler(), LinearRegression())  # 정규화를 모델 안에
model.fit(X_tr, y_tr)

mae = float(np.abs(model.predict(X_te) - y_te).mean())
baseline = float(np.abs(np.array([rule.estimate_delivery_minutes(a, b) for a, b in X_te]) - y_te).mean())
joblib.dump(model, "artifacts/sklearn.joblib")

json.dump({"model": "sklearn", "mae": mae, "baseline_mae": baseline},
          open("artifacts/metrics_sklearn.json", "w"), indent=2)
print(f"sklearn 평균 오차 {mae:.2f}분  (기준선 rule {baseline:.2f}분)")
