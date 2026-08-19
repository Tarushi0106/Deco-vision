import numpy as np
from insightface.app import FaceAnalysis

from . import face_db

SIMILARITY_THRESHOLD = 0.35


class FaceRecognizer:
    """Wraps InsightFace (buffalo_l) for detection + recognition against
    the enrolled faces pulled from the camera's onboard Allow List.
    """

    def __init__(self):
        self._app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        self._app.prepare(ctx_id=-1, det_size=(640, 640), det_thresh=0.5)
        self._reload_enrolled()

    def _reload_enrolled(self) -> None:
        self._enrolled = face_db.load_all_faces()

    @staticmethod
    def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))

    def _match(self, embedding: np.ndarray) -> tuple[str, float]:
        best_name, best_score = "Unknown", 0.0
        for name, ref_embedding in self._enrolled:
            score = self._cosine_sim(embedding, ref_embedding)
            if score > best_score:
                best_name, best_score = name, score
        if best_score < SIMILARITY_THRESHOLD:
            return "Unknown", best_score
        return best_name, best_score

    def detect_and_recognize(self, frame_bgr: np.ndarray) -> list[dict]:
        faces = self._app.get(frame_bgr)
        results = []
        for face in faces:
            name, score = self._match(face.embedding)
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            results.append(
                {
                    "bbox": [x1, y1, x2, y2],
                    "name": name,
                    "score": round(score, 3),
                }
            )
        return results
