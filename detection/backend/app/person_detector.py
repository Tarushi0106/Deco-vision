"""Plain YOLOv8n person detector (COCO class 0) — a new, independent model
instance for this standalone service, not shared with or imported from
Parachute's own pose_detector.py. class 0 only, so nothing that isn't a
person can ever appear as a "person" box.
"""

from ultralytics import YOLO

from . import config


class PersonDetector:
    def __init__(self):
        self._model = YOLO(config.PERSON_MODEL_WEIGHTS)

    def detect(self, frame_bgr) -> list[dict]:
        results = self._model.predict(
            frame_bgr, verbose=False, conf=config.PERSON_DETECT_CONF, classes=[0],
        )[0]
        if results.boxes is None:
            return []
        people = []
        for box in results.boxes:
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
            people.append({"bbox": [x1, y1, x2, y2], "confidence": round(float(box.conf[0]), 3)})
        return people
