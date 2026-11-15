import cv2
from ultralytics import YOLO

SOURCE = "test.mp4"      # use 0 instead of "test.mp4" to use your webcam
WANTED = [0, 2, 3, 5, 7]  # person, car, motorcycle, bus, truck
NAMES = {0: "person", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

model = YOLO("yolov8n.pt")   # the small, fast model (downloads the first time)
cap = cv2.VideoCapture("test.mp4")

if not cap.isOpened():
    raise SystemExit("Could not open the video. Check the file name and the folder.")

print("Running. Click the video window and press Q to quit.")

while True:
    ok, frame = cap.read()
    if not ok:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # video ended, start again
        continue

    # Make the picture smaller so it runs faster
    h, w = frame.shape[:2]
    frame = cv2.resize(frame, (960, int(960 * h / w)))

    # Ask the AI what it sees
    result = model.predict(frame, classes=WANTED, conf=0.4, verbose=False)[0]

    # Count each kind of object
    counts = {}
    for box in result.boxes:
        name = NAMES[int(box.cls[0])]
        counts[name] = counts.get(name, 0) + 1

    # Draw the boxes and write the counts on the picture
    shown = result.plot()
    text = ", ".join(f"{k}: {v}" for k, v in counts.items()) or "nothing detected"
    cv2.putText(shown, text, (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)

    cv2.imshow("Border camera - AI detection", shown)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()