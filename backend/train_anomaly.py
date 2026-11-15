import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

from anomaly import FEATURES, features, simulate_track

HERE = Path(__file__).resolve().parent
rng = np.random.default_rng(1)

PERSON_ODD = ["loitering", "running", "zigzag", "pacing"]
VEHICLE_ODD = ["stopped_vehicle", "erratic_vehicle"]
# (who, unusual kinds, normal tracks for training, normal tracks for each test set)
SETUP = [("person", PERSON_ODD, 3000, 750), ("vehicle", VEHICLE_ODD, 1000, 250)]


def build(kind, count):
    X = []
    for _ in range(count):
        points, veh = simulate_track(kind, rng)
        X.append(features(points, veh))
    return np.array(X)


def raw(model, X):
    return -model.score_samples(X)        # higher = more unusual


# One model for people and one for vehicles. Each learns NORMAL movement only.
models = {}
norm_scores, anom_scores, anom_kinds = [], [], []
base_norm, base_anom = [], []

for who, odd_kinds, n_train, n_test in SETUP:
    X_train = build(who, n_train)
    model = IsolationForest(n_estimators=200, random_state=1).fit(X_train)

    # threshold: only 2% of normal tracks may cross it (chosen on separate data)
    thr = float(np.percentile(raw(model, build(who, n_test)), 98))
    models[who] = {"model": model, "threshold": thr}

    # simple comparison rule: how far is the speed from the usual speed?
    mean, std = X_train[:, 1].mean(), X_train[:, 1].std()

    X_norm = build(who, n_test)
    norm_scores += list(raw(model, X_norm) / thr)
    base_norm += list(np.abs(X_norm[:, 1] - mean) / std)
    for k in odd_kinds:
        X_odd = build(k, 170)
        s = raw(model, X_odd) / thr
        anom_scores += list(s)
        base_anom += list(np.abs(X_odd[:, 1] - mean) / std)
        anom_kinds += [k] * len(s)

norm_scores, anom_scores = np.array(norm_scores), np.array(anom_scores)
anom_kinds = np.array(anom_kinds)
print(f"Trained a people model and a vehicle model on normal movement only.")
print(f"Testing on {len(norm_scores)} normal and {len(anom_scores)} unusual tracks.")

# On this scale 1.0 is the alert line for both kinds of object
y_true = np.array([0] * len(norm_scores) + [1] * len(anom_scores))
all_scores = np.concatenate([norm_scores, anom_scores])
y_pred = (all_scores >= 1.0).astype(int)

auc = roc_auc_score(y_true, all_scores)
auc_speed_rule = roc_auc_score(y_true, np.array(base_norm + base_anom))
precision = precision_score(y_true, y_pred)
recall = recall_score(y_true, y_pred)
f1 = f1_score(y_true, y_pred)
false_alarm = float((norm_scores >= 1.0).mean())

print(f"\nAlert line: score 1.0 (the 98th percentile of normal scores, for each kind)")
print(f"ROC-AUC                    : {auc:.3f}   (1.0 = perfect, 0.5 = guessing)")
print(f"Speed-only rule ROC-AUC    : {auc_speed_rule:.3f}   <- simple comparison")
print(f"Precision                  : {precision:.3f}   (of the alerts raised, how many were real)")
print(f"Recall                     : {recall:.3f}   (of the unusual tracks, how many were caught)")
print(f"F1 score                   : {f1:.3f}")
print(f"False alarm rate           : {false_alarm:.3f}   (normal tracks wrongly flagged)")
print("\nRecall for each kind of unusual movement:")
per_kind = {}
for k in PERSON_ODD + VEHICLE_ODD:
    r = float((anom_scores[anom_kinds == k] >= 1.0).mean())
    per_kind[k] = round(r, 3)
    print(f"  {k:16s} {r:.3f}")

joblib.dump({"models": models, "threshold": 1.0, "features": FEATURES}, HERE / "anomaly_model.joblib")
(HERE / "anomaly_metrics.json").write_text(json.dumps({
    "algorithm": "Isolation Forest (one model for people, one for vehicles)",
    "roc_auc": round(float(auc), 3), "speed_rule_auc": round(float(auc_speed_rule), 3),
    "precision": round(float(precision), 3), "recall": round(float(recall), 3),
    "f1": round(float(f1), 3), "false_alarm_rate": round(false_alarm, 3),
    "recall_by_kind": per_kind}, indent=2), encoding="utf-8")
print("\nSaved anomaly_model.joblib and anomaly_metrics.json")

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.figure(figsize=(7, 4))
    plt.hist(norm_scores, bins=40, alpha=0.7, label="normal tracks")
    plt.hist(anom_scores, bins=40, alpha=0.7, label="unusual tracks")
    plt.axvline(1.0, color="red", linestyle="--", label="alert line")
    plt.xlabel("anomaly score (1.0 = alert line)")
    plt.ylabel("number of tracks")
    plt.legend()
    plt.tight_layout()
    plt.savefig(HERE / "anomaly_scores.png", dpi=150)
    print("Saved anomaly_scores.png (use it in your report)")
except ImportError:
    print("(matplotlib is not installed, so no chart was drawn)")