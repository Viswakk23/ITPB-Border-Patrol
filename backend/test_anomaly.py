import numpy as np

from anomaly import features, load_model, score_features, simulate_track

bundle = load_model()
if bundle is None:
    raise SystemExit("Run python train_anomaly.py first.")

rng = np.random.default_rng(7)
limit = bundle["threshold"]
print(f"Alert threshold: {limit:.3f}\n")

for kind in ["person", "person", "vehicle", "loitering", "running",
             "zigzag", "pacing", "stopped_vehicle", "erratic_vehicle"]:
    points, veh = simulate_track(kind, rng)
    s = score_features(bundle, features(points, veh))
    print(f"{kind:16s} score {s:.3f}  ->  {'ANOMALY' if s >= limit else 'normal'}")
    