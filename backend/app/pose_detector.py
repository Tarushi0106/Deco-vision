"""Person detection + pose keypoints via YOLOv8n-pose — powers footfall,
fall detection, and intrusion presence-checking (see person_tracker.py and
pipeline.py). Deliberately not a general object detector: only the person
class is relevant to any of those three features, and no boxes from this
are shown as a visible "object detection" overlay anywhere.
"""

from ultralytics import YOLO

CONFIDENCE_THRESHOLD = 0.4


class PoseDetector:
    def __init__(self):
        self._model = YOLO("yolov8n-pose.pt")

    def detect(self, frame_bgr) -> list[dict]:
        """Returns [{"bbox": [x1,y1,x2,y2], "confidence": 0.87,
        "keypoints": [[x, y, conf], ...17 COCO keypoints] or None}, ...]"""
        results = self._model(frame_bgr, verbose=False)[0]
        people = []
        if results.boxes is None:
            return people
        for i, box in enumerate(results.boxes):
            confidence = float(box.conf[0])
            if confidence < CONFIDENCE_THRESHOLD:
                continue
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
            keypoints = None
            if results.keypoints is not None:
                keypoints = results.keypoints.data[i].tolist()
            people.append(
                {
                    "bbox": [x1, y1, x2, y2],
                    "confidence": round(confidence, 3),
                    "keypoints": keypoints,
                }
            )
        return people
