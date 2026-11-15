import json
import random
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

from sensors import read_sensors

HERE = Path(__file__).resolve().parent
rng = random.Random(1)

# 1. Make 20,000 examples of vehicles at different stages of wear
rows, answers = [], []
for _ in range(20000):
    wear = rng.uniform(0.04, 0.20)        # hidden: how fast this vehicle wears
    health = rng.uniform(32, 100)         # current health in percent
    temp, vib, volt = read_sensors(health, wear, rng)
    rows.append([health, temp, vib, volt])
    answers.append((health - 30) / wear)  # true km left until health reaches 30

X = np.array(rows)
y = np.array(answers)

# 2. Keep 20% aside to test the model on data it has never seen
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=1)
print(f"Training on {len(X_train)} examples, testing on {len(X_test)}")

# 3. Train
model = RandomForestRegressor(n_estimators=150, min_samples_leaf=3, n_jobs=-1, random_state=1)
model.fit(X_train, y_train)

# 4. Test, and compare with a simple guess that ignores the sensors
pred = model.predict(X_test)
baseline = (X_test[:, 0] - 30) / 0.12     # guess: every vehicle wears at the average speed
mae_model = mean_absolute_error(y_test, pred)
mae_base = mean_absolute_error(y_test, baseline)
r2 = r2_score(y_test, pred)

print(f"Simple guess (no AI):  average error = {mae_base:.0f} km")
print(f"AI model:              average error = {mae_model:.0f} km    R2 = {r2:.2f}")

joblib.dump(model, HERE / "rul_model.joblib")
(HERE / "rul_metrics.json").write_text(json.dumps({
    "algorithm": "Random Forest",
    "train_examples": int(len(X_train)),
    "test_examples": int(len(X_test)),
    "mae_ai_km": round(float(mae_model), 1),
    "mae_baseline_km": round(float(mae_base), 1),
    "r2": round(float(r2), 3),
}), encoding="utf-8")
print("Saved rul_model.joblib")