import numpy as np

def collect_data(n: int = 500, seed: int = 0):
    """배달 기록 생성. 같은 seed면 같은 데이터가 나온다."""
    rng = np.random.default_rng(seed)

    distance_km = rng.uniform(0.5, 8.0, n)
    pending_orders = rng.integers(0, 11, n).astype(float)

    minutes = (
        12
        + 3 * distance_km
        + 4 * pending_orders
        + rng.normal(0, 1.5, n)
    )

    X = np.column_stack(
        [distance_km, pending_orders]
    ).astype(np.float32)

    return X, minutes.astype(np.float32)