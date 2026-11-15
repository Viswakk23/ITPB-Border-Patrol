import random
from pathlib import Path

import joblib

from sensors import WEAR_BY_VEHICLE, read_sensors

HERE = Path(__file__).resolve().parent
_model = None


def load():
    global _model
    f = HERE / "rul_model.joblib"
    if _model is None and f.exists():
        _model = joblib.load(f)
    return _model


def predict(vehicle_id, health):
    # Returns (km left, (temperature, vibration, voltage)), or None if no model yet
    model = load()
    if model is None:
        return None
    wear = WEAR_BY_VEHICLE.get(vehicle_id, 0.08)
    rng = random.Random(f"{vehicle_id}-{int(health)}")
    temp, vib, volt = read_sensors(health, wear, rng)
    if health <= 30:
        return 0.0, (temp, vib, volt)
    km = float(model.predict([[health, temp, vib, volt]])[0])
    return max(0.0, km), (temp, vib, volt)