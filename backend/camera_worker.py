import time
from pathlib import Path

import cv2
import requests
from ultralytics import YOLO

from anomaly import MIN_POINTS, Tracker, features, load_model, score_features

HERE = Path(__file__).resolve().parent
API = "http://127.0.0.1:8000"
VIDEO = "test.mp4"       # the video every camera uses
COOLDOWN = 20            # seconds between two alerts from the same camera
NEEDED = 5               # frames in a row before we trust a zone intrusion
SKIP = 6                 # skip frames so the video moves at a natural speed
ANOMALY_STEPS = 3        # unusual readings in a row before a track counts as anomalous
THRESHOLD_OVERRIDE = None   # for a demo you can set e.g. 0.50 to make it more sensitive

# zone   = restricted zone: left, top, right, bottom (0 to 1)
# alerts = may this camera raise alerts? (False = it only watches and shows boxes)
CAMERAS = {
    "CAM-1": {"zone": (0.0, 0.0, 1.0, 1.0), "alerts": True},
    "CAM-2": {"zone": (0.35, 0.30, 1.0, 1.0), "alerts": False},
    "CAM-3": {"zone": (0.35, 0.30, 1.0, 1.0), "alerts": False},
}

NAMES = {0: "person", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


def video_for(name):
    own = HERE / (name.lower().replace("-", "") + ".mp4")
    return str(own if own.exists() else HERE / VIDEO)


print("Loading the AI model...")
model = YOLO("yolov8n.pt")

bundle = load_model()
if bundle:
    THRESHOLD = THRESHOLD_OVERRIDE or bundle["threshold"]
    print(f"Anomaly model loaded. Alert threshold = {THRESHOLD:.3f}")
else:
    THRESHOLD = None
    print("No anomaly model found (run train_anomaly.py). Using the zone rule only.")

cams = {}
for i, (name, cfg) in enumerate(CAMERAS.items()):
    cap = cv2.VideoCapture(video_for(name))
    if not cap.isOpened():
        print("Could not open the video for", name)
        continue
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(max(frames, 1) * i / len(CAMERAS)))
    cams[name] = {"cap": cap, "cfg": cfg, "is_file": frames > 0, "tracker": Tracker(),
                  "streak": 0, "last_alert": 0, "banner": 0}

if not cams:
    raise SystemExit("No video could be opened. Put test.mp4 in the backend folder.")

session = requests.Session()
last_warning = 0


def process(name, c, frame, t):
    now = time.time()
    h, w = frame.shape[:2]
    frame = cv2.resize(frame, (640, int(640 * h / w)))
    h, w = frame.shape[:2]
    z = c["cfg"]["zone"]
    zx1, zy1, zx2, zy2 = int(z[0] * w), int(z[1] * h), int(z[2] * w), int(z[3] * h)

    layer = frame.copy()
    cv2.rectangle(layer, (zx1, zy1), (zx2, zy2), (0, 0, 255), -1)
    frame = cv2.addWeighted(layer, 0.12, frame, 0.88, 0)
    cv2.rectangle(frame, (zx1, zy1), (zx2, zy2), (0, 0, 255), 2)

    result = model.predict(frame, classes=list(NAMES), conf=0.4, imgsz=480, verbose=False)[0]

    # 1. What did YOLO see?
    boxes, dets = [], []
    for box in result.boxes:
        cls = int(box.cls[0])
        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
        fx, fy = (x1 + x2) / 2, y2                      # the foot point
        boxes.append((cls, x1, y1, x2, y2, fx, fy))
        dets.append((fx / w, fy / h, cls != 0))

    # 2. Follow each object over time
    tracks = c["tracker"].update(dets, t)

    people = vehicles = 0              # inside the zone
    odd_people = odd_vehicles = 0      # with unusual movement
    for (cls, x1, y1, x2, y2, fx, fy), tr in zip(boxes, tracks):
        inside = zx1 <= fx <= zx2 and zy1 <= fy <= zy2

        # 3. Judge the movement each time the track gets a new point
        if THRESHOLD is not None and tr["new_sample"] and len(tr["pts"]) >= MIN_POINTS:
            tr["score"] = score_features(bundle, features(tr["pts"], tr["veh"]))
            tr["bad"] = tr["bad"] + 1 if tr["score"] >= THRESHOLD else 0
        odd = tr["bad"] >= ANOMALY_STEPS

        color = (0, 140, 255) if odd else (0, 0, 255) if inside else (0, 200, 0)
        label = f"{NAMES[cls]} #{tr['id']}"
        if tr["score"] is not None:
            label += f" {tr['score']:.2f}"
        if odd:
            label = "ANOMALY " + label
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, label, (x1, max(12, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

        if inside:
            if cls == 0:
                people += 1
            else:
                vehicles += 1
        if odd:
            if cls == 0:
                odd_people += 1
            else:
                odd_vehicles += 1

    c["streak"] = c["streak"] + 1 if (people + vehicles) > 0 else 0
    odd_total = odd_people + odd_vehicles

    # 4. Raise an alert
    if c["cfg"]["alerts"] and now - c["last_alert"] > COOLDOWN:
        payload = None
        if c["streak"] >= NEEDED:
            payload = {"camera": name, "people": people, "vehicles": vehicles,
                       "kind": "Intrusion in restricted zone", "anomaly": odd_total > 0}
        elif odd_total > 0:
            payload = {"camera": name, "people": odd_people, "vehicles": odd_vehicles,
                       "kind": "Anomalous movement", "anomaly": True}
        if payload:
            try:
                r = session.post(API + "/alerts", json=payload, timeout=5)
                print(f"{name} ALERT SENT: {r.json()['severity']} - {payload['kind']}")
                c["banner"] = now + 3
            except Exception:
                print("Could not reach the server. Is uvicorn running?")
            c["last_alert"] = now

    cv2.putText(frame, f"{name}   {time.strftime('%H:%M:%S')}", (10, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    cv2.putText(frame, f"in zone: {people} people, {vehicles} vehicles   unusual: {odd_total}",
                (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    if now < c["banner"]:
        cv2.putText(frame, "ALERT SENT", (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 3)
    return frame


print("Camera wall running:", ", ".join(cams), "(press Ctrl+C to stop)")
try:
    while True:
        for name, c in cams.items():
            cap = c["cap"]
            for _ in range(SKIP):
                cap.grab()
            got, frame = cap.read()
            if not got:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                c["tracker"].reset()
                continue

            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 if c["is_file"] else time.time()
            frame = process(name, c, frame, t)

            done, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            try:
                session.post(f"{API}/camera/{name}/frame", data=jpg.tobytes(),
                             headers={"Content-Type": "image/jpeg"}, timeout=2)
            except Exception:
                if time.time() - last_warning > 10:
                    print("Could not reach the server. Is uvicorn running?")
                    last_warning = time.time()
except KeyboardInterrupt:
    print("Stopped.")