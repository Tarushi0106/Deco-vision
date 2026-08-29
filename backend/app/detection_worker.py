"""Runs face recognition and pose-based footfall/fall analysis in a
SEPARATE PROCESS from the FastAPI app.

Measured this session: onnxruntime's inference call does not release the
GIL for its full wall-clock duration, so running it in a thread of the
main process — even a "separate" one — still froze the asyncio loop
serving /ws/live for as long as each call ran (100-300ms), no matter how
that call's own thread pool was tuned. A real OS process has its own GIL;
nothing it does can block the main process's event loop. The only contact
between the two is the queues passed into run_worker() below, carrying
small payloads (JPEG bytes in, small result dicts out) — never raw frames
or model objects.

DB access (writing detection/footfall/alert rows, reading intrusion
settings) stays in the main process, which already owns those modules;
this worker is pure compute and knows nothing about SQLite.
"""

import queue
import time

import cv2
import numpy as np
import onnxruntime
from insightface.app.common import Face

POSE_INTERVAL_SECONDS = 20.0  # pose is heavier than face rec; runs on its own slower cadence.
# Raised from 5.0 -> 20.0: on CPU, pose (YOLOv8n-pose) + face rec both running
# in this single worker process per "detect" call could take longer than the
# 1s gap between frames, backing up the input queue (maxsize=10) and making
# recognized names lag several seconds behind real time. Running pose less
# often keeps the worker caught up so face-rec results stay near real-time.

DEFAULT_DET_THRESH = 0.65
# Per-camera detection-confidence floor, overriding DEFAULT_DET_THRESH. One
# global threshold can't fit every camera: measured real faces score 0.78-0.87
# on camera 1 (Entry/Exit, a close head-on view — a high floor cuts off the
# occasional phantom detection from patterned wall graphics without touching
# real faces), but only 0.5-0.76 on camera 2 (Technical section, a wide
# overview of ~8 people at a distance — the same high floor there was
# discarding most of the real, just-naturally-smaller faces in frame).
CAMERA_DET_THRESH = {
    2: 0.45,  # Technical section: wide-angle, many small/distant faces
}

# Detection (bbox localization) is cheap and works fine on a downscaled copy,
# but RECOGNITION quality does not: InsightFace crops its 112x112 alignment
# patch from whatever image is passed to FaceAnalysis.get(), not from the
# detector's internal resized copy — so recognition run directly on a
# downscaled multi-person frame starves every face of real detail. Measured
# on the Technical section camera: a verified real match went from 0.64
# similarity (recognized on a full-res crop of just that face) to ~0.06-0.2
# (recognized directly on the 640px-downscaled whole frame) — the entire
# cause of real, enrolled people showing as Visitor there.
# Running recognition on the full-res frame for EVERY face fixes this but
# costs ~1-1.5s per face (measured: 8 faces on a 2880x1620 frame took 11+
# seconds) — far too slow shared across 3 cameras. The compromise: detect
# cheaply on a downscaled copy (fast, fine for locating everyone), then
# re-run full recognition on a padded full-res crop for only the largest
# few faces each cycle — the people actually close enough to the camera to
# be worth identifying. Smaller/farther faces keep the cheap (lower-quality)
# result rather than nobody's face getting the expensive treatment.
# Raised from 640: a real, fully-frontal, unobstructed face on the Technical
# section camera (2880x1620) was getting dropped by the detector ENTIRELY at
# that resolution — not a low-confidence match, zero detection at all — while
# a person with their back completely turned (no visible face) triggered a
# false-positive "detection". Both point to the same cause: too little detail
# left after a ~4.5x downscale on a wide multi-person scene. Must move
# together with recognizer.py's det_size — see the comment there. Paid for by
# switching inference to DirectML (recognizer.py), not by cutting update rate.
DETECTION_DOWNSCALE_MAX_DIM = 1280
# Accuracy prioritized over update speed per explicit request. Measured on a
# real 6-person Technical section frame: rechecking only the largest 3 faces
# missed 2 of 3 real, strong matches (0.63, 0.49, 0.49 similarity) because
# face SIZE in frame doesn't predict who's actually identifiable — the
# closest people to the camera aren't necessarily the ones with a good
# enrolled sample. Raised to cover a full room instead of just the closest
# few. The "name == Unknown" guard below still means an already-confident
# cheap-pass match skips its recheck, so the budget only ever goes to
# genuinely uncertain faces — this mainly costs more when MANY people are
# simultaneously unmatched, not per person in frame.
MAX_FULL_RES_RECHECKS = 8
RECHECK_CROP_PADDING = 0.8  # extra margin around a face's bbox, as a fraction of its own size
RECHECK_DET_THRESH = 0.3  # lenient: an isolated single-face crop needs less context to detect than a full scene

# Enrollment photos (manual upload, or a camera's onboard Allow List export)
# are typically small, already-cropped headshots (measured: 256x256, ~5KB
# JPEGs from this camera's Allow List) - a completely different shape than
# the large multi-person live frames recognizer.py's det_size=1280 was raised
# for. Detecting at 1280 upscales a 256px image 5x, degrading it enough that
# det_score collapsed from 0.544 (at 640) to 0.145 on a real, clear, frontal
# enrollment photo - below even the lenient 0.3 threshold below, silently
# failing every camera Allow List sync with a misleading "no face detected".
ENROLLMENT_DET_SIZE = (640, 640)


def _lean_recognize_crop(recognizer, crop):
    """Detection + recognition only on an isolated single-face crop, skipping
    the landmark_3d_68/landmark_2d_106/genderage models that FaceAnalysis.get()
    would otherwise also run. ArcFaceONNX.get() (see arcface_onnx.py) only
    ever reads face.kps — the detector's own 5-point keypoints — so those
    other three models contribute nothing to the recognition decision;
    running them anyway on every recheck was most of the added cost that
    stalled the worker under MAX_FULL_RES_RECHECKS>1 on a busy camera."""
    bboxes, kpss = recognizer._app.det_model.detect(crop, max_num=0, metric="default")
    if bboxes.shape[0] == 0 or kpss is None:
        return None
    idx = int(np.argmax((bboxes[:, 2] - bboxes[:, 0]) * (bboxes[:, 3] - bboxes[:, 1])))
    face = Face(bbox=bboxes[idx, 0:4], kps=kpss[idx], det_score=bboxes[idx, 4])
    recognizer._app.models["recognition"].get(crop, face)
    return face


def _detect_and_recognize(recognizer, frame, det_thresh: float) -> list[dict]:
    h, w = frame.shape[:2]
    scale = min(1.0, DETECTION_DOWNSCALE_MAX_DIM / max(h, w))
    small = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1.0 else frame

    recognizer._app.det_model.det_thresh = det_thresh
    faces = recognizer._app.get(small)
    for f in faces:
        f.bbox = f.bbox / scale
    faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)

    results = []
    rechecks_used = 0
    for f in faces:
        x1, y1, x2, y2 = [int(v) for v in f.bbox]
        name, score = recognizer._match(f.embedding)

        # A face the cheap pass already matched confidently doesn't need the
        # expensive recrop — spending the (limited, costly) recheck budget on
        # it would just confirm what's already known, at the expense of an
        # actually-uncertain face that might otherwise have gotten it.
        if name == "Unknown" and rechecks_used < MAX_FULL_RES_RECHECKS:
            rechecks_used += 1
            bw, bh = x2 - x1, y2 - y1
            pw, ph = int(bw * RECHECK_CROP_PADDING), int(bh * RECHECK_CROP_PADDING)
            crop = frame[max(0, y1 - ph):y2 + ph, max(0, x1 - pw):x2 + pw]
            if crop.size > 0:
                recognizer._app.det_model.det_thresh = RECHECK_DET_THRESH
                refined = _lean_recognize_crop(recognizer, crop)
                if refined is not None:
                    r_name, r_score = recognizer._match(refined.embedding)
                    if r_score > score:
                        name, score = r_name, r_score

        results.append({"bbox": [x1, y1, x2, y2], "name": name, "score": round(score, 3)})
    return results


def _cap_inference_threads(max_cpu_cores: int) -> None:
    """Pins internal thread-pool sizes to the same core budget as the
    process's OS-level CPU affinity (see PipelineManager._pin_worker_cpu_affinity).

    Measured this session: onnxruntime and PyTorch both size their default
    thread pool to the SYSTEM's total logical CPU count at model-load time —
    they never look at the process's actual affinity mask. Restricting
    affinity alone left ~12 threads spin-waiting for time on only 4 allowed
    cores, which is far slower than 12 threads on 12 cores would ever be
    (thread-pool oversubscription with busy-spin waiting, not a modest
    slowdown) — a single face embedding went from under a second to well
    past the caller's 10s timeout, wrongly reported as "no face detected".
    Sizing every library's thread pool to match the real core budget avoids
    that; OS affinity then just enforces the same limit as a backstop.
    """
    cv2.setNumThreads(max_cpu_cores)

    import torch
    torch.set_num_threads(max_cpu_cores)

    original_init = onnxruntime.InferenceSession.__init__

    def _patched_init(self, path_or_bytes, sess_options=None, **kwargs):
        if sess_options is None:
            sess_options = onnxruntime.SessionOptions()
        sess_options.intra_op_num_threads = max_cpu_cores
        sess_options.inter_op_num_threads = 1
        original_init(self, path_or_bytes, sess_options=sess_options, **kwargs)

    onnxruntime.InferenceSession.__init__ = _patched_init


def run_worker(
    input_queue: "queue.Queue",
    result_queue: "queue.Queue",
    embed_response_queue: "queue.Queue",
    embed_request_queue: "queue.Queue",
    max_cpu_cores: int,
) -> None:
    _cap_inference_threads(max_cpu_cores)

    # imported here, after the onnxruntime patch above is in place, so every
    # model these construct picks up the capped thread pool
    from .person_tracker import PersonTracker
    from .pose_detector import PoseDetector
    from .recognizer import FaceRecognizer

    recognizer = FaceRecognizer()
    pose_detector = PoseDetector()
    trackers: dict[int, PersonTracker] = {}
    last_pose_at: dict[int, float] = {}

    while True:
        # Checked first, non-blocking, every iteration: embed (enrollment) requests
        # are rare but the caller is waiting synchronously on a short deadline (see
        # PipelineManager.compute_embedding). They must never sit behind the
        # constant stream of "detect" frames on input_queue, or they time out
        # before the worker ever gets to them even though detection itself
        # would have succeeded.
        try:
            item = embed_request_queue.get_nowait()
        except queue.Empty:
            try:
                item = input_queue.get(timeout=0.5)
            except queue.Empty:
                continue

        kind = item.get("type")

        if kind == "reload_faces":
            recognizer._reload_enrolled()
            continue

        if kind == "embed":
            frame = cv2.imdecode(np.frombuffer(item["jpeg"], np.uint8), cv2.IMREAD_COLOR)
            embedding = None
            if frame is not None:
                # Enrollment photos are single, still, controlled shots (unlike
                # continuous live frames) - safe to detect leniently here without
                # raising false positives in the live per-frame feed, which stays
                # at the stricter default threshold.
                det_model = recognizer._app.det_model
                original_thresh = det_model.det_thresh
                original_sizes = det_model.input_sizes
                original_size = det_model.input_size
                det_model.det_thresh = 0.3
                det_model.input_sizes = [ENROLLMENT_DET_SIZE]
                det_model.input_size = ENROLLMENT_DET_SIZE
                try:
                    faces = recognizer._app.get(frame)
                finally:
                    det_model.det_thresh = original_thresh
                    det_model.input_sizes = original_sizes
                    det_model.input_size = original_size
                if faces:
                    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                    embedding = face.embedding
            embed_response_queue.put({"request_id": item["request_id"], "embedding": embedding})
            continue

        if kind == "detect":
            camera_id = item["camera_id"]
            frame = cv2.imdecode(np.frombuffer(item["jpeg"], np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue

            det_thresh = CAMERA_DET_THRESH.get(camera_id, DEFAULT_DET_THRESH)
            result = {"camera_id": camera_id, "faces": _detect_and_recognize(recognizer, frame, det_thresh)}

            now = time.time()
            if now - last_pose_at.get(camera_id, 0) >= POSE_INTERVAL_SECONDS:
                last_pose_at[camera_id] = now
                people = pose_detector.detect(frame)
                tracker = trackers.setdefault(camera_id, PersonTracker())
                events = tracker.update(people, frame.shape[0])
                result["footfall_events"] = events["footfall_events"]
                result["fall_events"] = events["fall_events"]
                result["person_count"] = len(people)

            try:
                result_queue.put_nowait(result)
            except queue.Full:
                pass
