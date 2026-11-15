import math
from pathlib import Path

import joblib
import numpy as np

HERE = Path(__file__).resolve().parent

FEATURES = ["is_vehicle", "speed", "speed_var", "straightness", "turning", "still_fraction"]
SAMPLE_DT = 0.5      # a track gets one point every 0.5 seconds of video
WINDOW = 12          # judge a track by its last 12 points (about 6 seconds)
MIN_POINTS = 6       # need at least 6 points before judging
STILL_SPEED = 0.02   # below this speed (frame widths per second) we say "standing still"


# ---------- 1. Describe a movement with 6 numbers ----------
def features(points, is_vehicle):
    # points = list of (time, x, y), with x and y between 0 and 1
    a = np.array(points[-WINDOW:], dtype=float)
    t, x, y = a[:, 0], a[:, 1], a[:, 2]
    dt = np.maximum(np.diff(t), 1e-3)
    dx, dy = np.diff(x), np.diff(y)
    step = np.hypot(dx, dy)
    sp = step / dt                                     # speed of each step

    speed = float(sp.mean())
    speed_var = float(min(3.0, sp.std() / (speed + 1e-6)))
    path = float(step.sum())
    net = float(np.hypot(x[-1] - x[0], y[-1] - y[0]))
    straightness = min(1.0, net / (path + 1e-6))       # 1 = perfectly straight line

    ang = np.arctan2(dy, dx)
    turning = 0.0
    if len(ang) > 1:
        change = np.abs((np.diff(ang) + np.pi) % (2 * np.pi) - np.pi)
        turning = float(change.mean())                 # average change of direction
    still = float((sp < STILL_SPEED).mean())

    return [1.0 if is_vehicle else 0.0, speed, speed_var, straightness, turning, still]


# ---------- 2. A simple tracker: follows each object between frames ----------
class Tracker:
    def __init__(self, max_dist=0.12, max_gap=2.0):
        self.tracks = {}
        self.next_id = 1
        self.max_dist = max_dist
        self.max_gap = max_gap
        self.last_t = None

    def reset(self):
        self.tracks = {}
        self.last_t = None

    def update(self, dets, t):
        # dets = list of (x, y, is_vehicle). Returns one track per detection, same order.
        if self.last_t is not None and t < self.last_t:
            self.reset()                               # the video started again
        self.last_t = t
        for tr in self.tracks.values():
            tr["new_sample"] = False

        # match each detection to the nearest track of the same kind
        pairs = []
        for di, (x, y, veh) in enumerate(dets):
            for tid, tr in self.tracks.items():
                if tr["veh"] != veh:
                    continue
                d = math.hypot(x - tr["x"], y - tr["y"])
                if d <= self.max_dist:
                    pairs.append((d, di, tid))
        pairs.sort()
        out = [None] * len(dets)
        used = set()
        for d, di, tid in pairs:
            if out[di] is None and tid not in used:
                out[di] = self.tracks[tid]
                used.add(tid)

        # unmatched detections start new tracks
        for di, (x, y, veh) in enumerate(dets):
            if out[di] is None:
                tr = {"id": self.next_id, "veh": veh, "pts": [], "x": x, "y": y,
                      "seen": t, "bad": 0, "score": None, "new_sample": False}
                self.next_id += 1
                self.tracks[tr["id"]] = tr
                out[di] = tr

        for di, (x, y, veh) in enumerate(dets):
            tr = out[di]
            tr["x"], tr["y"], tr["seen"] = x, y, t
            if not tr["pts"] or t - tr["pts"][-1][0] >= SAMPLE_DT:
                tr["pts"].append((t, x, y))
                tr["pts"] = tr["pts"][-WINDOW:]
                tr["new_sample"] = True

        for tid in [k for k, tr in self.tracks.items() if t - tr["seen"] > self.max_gap]:
            del self.tracks[tid]                       # not seen for a while: forget it
        return out


# ---------- 3. Make example movements (used for training and testing) ----------
# kind: (lowest speed, highest speed, direction noise)
PARAMS = {
    "person":          (0.04, 0.14, 0.15),   # normal: a person walking
    "vehicle":         (0.15, 0.45, 0.05),   # normal: a vehicle driving
    "loitering":       (0.00, 0.012, 3.0),   # unusual: hanging around in one spot
    "running":         (0.30, 0.60, 0.15),   # unusual: a person moving very fast
    "zigzag":          (0.05, 0.15, 1.2),    # unusual: erratic, changing direction
    "pacing":          (0.05, 0.12, 0.10),   # unusual: walking back and forth
    "stopped_vehicle": (0.00, 0.010, 0.5),   # unusual: a vehicle parked in view
    "erratic_vehicle": (0.10, 0.40, 1.0),    # unusual: a vehicle swerving
}
NORMAL_KINDS = ["person", "vehicle"]
ANOMALY_KINDS = ["loitering", "running", "zigzag", "pacing", "stopped_vehicle", "erratic_vehicle"]


def simulate_track(kind, rng):
    lo, hi, noise = PARAMS[kind]
    is_vehicle = "vehicle" in kind
    speed = rng.uniform(lo, hi)
    heading = rng.uniform(0, 2 * np.pi)
    x, y = rng.uniform(0.2, 0.8), rng.uniform(0.3, 0.9)
    n = int(rng.integers(8, 17))
    pause_at = int(rng.integers(2, n - 2)) if (kind == "person" and rng.random() < 0.2) else -1

    points, t = [], 0.0
    for i in range(n):
        points.append((t, x + rng.normal(0, 0.004), y + rng.normal(0, 0.004)))
        heading += rng.normal(0, noise)
        if kind == "pacing" and i % 3 == 2:
            heading += np.pi                           # turn around
        v = max(0.0, speed * (1 + rng.normal(0, 0.1)))
        if pause_at >= 0 and i in (pause_at, pause_at + 1):
            v = 0.0                                    # a normal person may stop briefly
        x += np.cos(heading) * v * SAMPLE_DT
        y += np.sin(heading) * v * SAMPLE_DT
        t += SAMPLE_DT
    return points, is_vehicle


# ---------- 4. Use the trained model ----------
def load_model():
    f = HERE / "anomaly_model.joblib"
    return joblib.load(f) if f.exists() else None


def score_features(bundle, feats):
    # higher = more unusual. 1.0 is exactly the alert line for that kind of object.
    kind = "vehicle" if feats[0] >= 0.5 else "person"
    m = bundle["models"][kind]
    raw = float(-m["model"].score_samples([feats])[0])
    return raw / m["threshold"]