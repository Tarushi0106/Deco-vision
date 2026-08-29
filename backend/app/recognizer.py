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
        # Benchmarked on this machine's real hardware (Intel Iris Xe iGPU, no
        # discrete GPU): DirectML ran det_10g at ~92ms and w600k_r50 at ~122ms
        # vs. 557ms/587ms on CPU — a 5-6x speedup, measured even while the CPU
        # was already busy with capture/API/other processes (onnxruntime-directml
        # is now the installed package; CPUExecutionProvider stays listed as a
        # fallback for any op DirectML doesn't support, and for machines with
        # no usable GPU at all). This budget is what pays for the higher
        # det_size below without reintroducing the CPU-oversubscription lag
        # this session already fixed once.
        self._app = FaceAnalysis(name="buffalo_l", providers=["DmlExecutionProvider", "CPUExecutionProvider"])
        # Raised from 640: on the Technical section camera (2880x1620, wide
        # multi-person view), a fully frontal, unobstructed face was measured
        # getting ZERO detection at 640 — det_size that small only gives a
        # face that occupies a small fraction of a wide scene a handful of
        # real pixels to work with. Must move together with
        # detection_worker.DETECTION_DOWNSCALE_MAX_DIM — that pre-resize
        # happens BEFORE the frame ever reaches this det_size, so raising
        # only one of the two just moves the same bottleneck, it doesn't
        # remove it.
        #
        # Measured on the live Entry/Exit camera: real faces score 0.78-0.87
        # det_score, but at the old 0.5 threshold this office's busy patterned
        # wall graphics/glass reflections occasionally clear the bar as a
        # phantom "face" with no real person behind it — and with enough
        # enrolled people, a phantom's noise-level embedding has a real chance
        # of randomly exceeding SIMILARITY_THRESHOLD against one of them,
        # surfacing as a wrong name on an empty patch of wall. Raised to keep
        # full margin below real faces while cutting off that low-confidence
        # band the phantoms lived in.
        self._app.prepare(ctx_id=-1, det_size=(1280, 1280), det_thresh=0.65)
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
