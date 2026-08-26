import numpy as np
from insightface.app import FaceAnalysis

from . import face_db

# Lowered from 0.35: most enrolled people (32 of 36 at last count) only have
# 1-2 low-quality photos pulled from a camera's onboard Allow List, not a
# real enrollment photo — their true live-camera similarity often sits in
# the low-mid 0.30s rather than comfortably above 0.35. Measured a real,
# correct match ("Satyendra") scoring 0.332 — a genuine person, just missed
# by a few hundredths. Genuinely wrong/coincidental matches observed this
# session topped out around 0.28, so 0.32 recovers cases like Satyendra's
# without (as far as measured) letting those coincidental ones through.
SIMILARITY_THRESHOLD = 0.32


class FaceRecognizer:
    """Wraps InsightFace (buffalo_l) for detection + recognition against
    the enrolled faces pulled from the camera's onboard Allow List.
    """

    def __init__(self):
        # Tried DmlExecutionProvider (Intel Iris Xe iGPU) this session: an
        # isolated micro-benchmark of det_10g/w600k_r50 alone on synthetic
        # input showed a promising 5-6x speedup, but the REAL pipeline
        # (FaceAnalysis.get() chaining 5 models — detection, landmark_3d_68,
        # landmark_2d_106, genderage, recognition — per face, at higher
        # resolution, on a real frame) measured 30-38 SECONDS per frame on
        # DirectML, worse than CPU by a huge margin — froze live detection
        # results entirely. Reverted. A single isolated model's synthetic
        # benchmark does not predict this pipeline's real behavior; don't
        # trust one without re-measuring the actual end-to-end call.
        #
        # Tried capping intra_op_num_threads here to reduce peak CPU% per
        # call — measurement showed it backfired: the real problem is each
        # call blocking the GIL/event loop for its full wall-clock duration,
        # and fewer threads means each call takes LONGER to finish (more
        # blocked time), not less. Left at onnxruntime's default (use
        # available cores, finish fast) and controlling total impact via
        # DETECTION_INTERVAL_SECONDS/PERSON_ANALYSIS_INTERVAL_SECONDS
        # (how often it's called) instead, in pipeline.py.
        self._app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        # Measured on the live Entry/Exit camera: real faces score 0.78-0.87
        # det_score, but at the old 0.5 threshold this office's busy patterned
        # wall graphics/glass reflections occasionally clear the bar as a
        # phantom "face" with no real person behind it — and with enough
        # enrolled people, a phantom's noise-level embedding has a real chance
        # of randomly exceeding SIMILARITY_THRESHOLD against one of them,
        # surfacing as a wrong name on an empty patch of wall. Raised to keep
        # full margin below real faces while cutting off that low-confidence
        # band the phantoms lived in.
        self._app.prepare(ctx_id=-1, det_size=(640, 640), det_thresh=0.65)
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
