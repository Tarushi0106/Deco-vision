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

import logging
import queue
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime
from insightface.app.common import Face

from . import config

logger = logging.getLogger("dashboard.recognition_pipeline")

# Fire/smoke detection (fire_smoke_detector.py) is a color/motion heuristic,
# not a trained model — every real false positive found so far (static
# walls/glass/floors, a person fidgeting at a desk) needed an actual frame
# to diagnose; guessing at synthetic reproductions wastes time and doesn't
# always match the real failure. Saving the exact analyzed frame whenever an
# alert actually fires means the NEXT false positive comes with real
# evidence instead of another guessing round.
FIRE_SMOKE_DEBUG_DIR = Path(__file__).resolve().parent.parent / "data" / "fire_smoke_debug"
# fire_smoke_detector's events now re-fire every cycle for as long as a
# condition stays confirmed (real fire/smoke should keep re-alerting, not
# go silent after one shot — see pipeline.FIRE_SMOKE_ALERT_COOLDOWN_SECONDS),
# so this needs its own throttle or a persisting event would write a debug
# frame to disk roughly once a second forever.
FIRE_SMOKE_DEBUG_SAVE_INTERVAL_SECONDS = 60

POSE_INTERVAL_SECONDS = 20.0  # pose is heavier than face rec; runs on its own slower cadence.
# Raised from 5.0 -> 20.0: on CPU, pose (YOLOv8n-pose) + face rec both running
# in this single worker process per "detect" call could take longer than the
# 1s gap between frames, backing up the input queue (maxsize=10) and making
# recognized names lag several seconds behind real time. Running pose less
# often keeps the worker caught up so face-rec results stay near real-time.

DEFAULT_DET_THRESH = config.RECOGNITION_DET_THRESH_DEFAULT
# Per-camera detection-confidence floor, overriding DEFAULT_DET_THRESH. One
# global threshold can't fit every camera: measured real faces score 0.78-0.87
# on camera 1 (Entry/Exit, a close head-on view — a high floor cuts off the
# occasional phantom detection from patterned wall graphics without touching
# real faces), but only 0.5-0.76 on camera 2 (Technical section, a wide
# overview of ~8 people at a distance — the same high floor there was
# discarding most of the real, just-naturally-smaller faces in frame).
#
# Camera 1's own 0.65 floor turned out to only fit the CLOSE reception-desk
# framing it was measured against — the actual entry/exit doorway (where the
# footfall gate line lives, see gate_tracker.py) is much farther from the
# camera and backlit by a large window behind it, both of which push a real
# face's detection score down. Confirmed live: a person walked through that
# doorway and produced zero detection events on camera 1 for several minutes
# straight while cameras 2/3 kept detecting normally in the same window — not
# a stream or gate-logic problem, camera 1 simply never saw a face there at
# all. Lowered enough to catch a distant/backlit doorway face; the wall
# graphic that motivated the original high floor sits on the far left of the
# frame (x < ~0.35), well outside the gate line's x-range (~0.43-0.70), so it
# has no path to spuriously trigger a gate crossing even if a phantom
# detection slips through here.
CAMERA_DET_THRESH = config.CAMERA_DET_THRESH_OVERRIDES or {
    1: 0.5,  # Entry/Exit: doorway crossing is far from camera and backlit
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
# Tried raising this to 1280 (paired with recognizer.py's det_size and a
# DirectML switch) to fix a real bug on the Technical section camera: a
# fully-frontal, unobstructed face got ZERO detection at 640, while a
# back-turned head with no visible face triggered a false positive. The fix
# direction was right, but DirectML made the real per-frame cost 30-38s
# instead of faster (see recognizer.py) — reverted both together rather than
# ship a resolution bump on CPU alone untested, since that combination was
# never actually measured on its own.
#
# Finally measured on CPU alone (no DirectML) against two real saved frames
# from this camera (5 and 8 people, most seated/looking down at laptops —
# room_check.jpg/room_now2.jpg): at 640, detection found only 1-2 of 5 faces
# and 6 of 8; at 1280, it found 4 of 5 (the only miss was a person almost
# fully turned away, no face visible at all — not fixable by resolution) and
# a clean 8 of 8 on the busier frame, with zero false positives observed
# down to a 0.35 threshold on either image (the earlier "back-turned head"
# false positive did not reproduce here — most likely a DirectML-path
# artifact, or a different det_thresh/frame, not this resolution itself on
# CPU). Cost: the main detect() call went from ~1-1.4s to ~3-3.6s per frame
# for this camera specifically — a real, deliberate trade of camera 2's own
# update latency (and, since the worker processes one frame at a time
# across every camera, a few seconds of added latency for cameras 1/3 while
# camera 2's frame is being processed) for actually seeing everyone in a
# room this wide. Not applied globally: cameras 1/3 don't have this problem
# and shouldn't pay this cost. See CAMERA_DETECTION_MAX_DIM below.
DETECTION_DOWNSCALE_MAX_DIM = 640
CAMERA_DETECTION_MAX_DIM = config.CAMERA_DETECTION_MAX_DIM_OVERRIDES or {
    2: 1280,  # Technical section: wide-angle room, up to ~8 people — see measurement above
}

# Confirmed real false positives (2026-08-31, see backend/data/fire_smoke_debug/
# cam1_smoke_*.jpg — auto-saved by _save_fire_smoke_debug_frame below): the
# same physical white pillar/doorway-frame beside camera 1's backlit glass
# entrance was flagged as "smoke" twice, a minute apart. That exact spot sits
# right next to a large glass door/window with constantly shifting natural
# light — real, gradual brightness drift there looks exactly like a slowly
# growing achromatic haze to a color/motion heuristic (fire_smoke_detector.py
# has no model, just color+motion math), and no amount of global threshold
# tuning can fix that without risking missing real smoke elsewhere. Statically
# excluded here, camera-specific, the same way CAMERA_DET_THRESH/
# CAMERA_DETECTION_MAX_DIM above are camera-specific corrections for a
# real, observed per-camera condition. Fractional (0..1) [x1,y1,x2,y2],
# independent of the camera's actual frame resolution.
CAMERA_FIRE_SMOKE_IGNORE_REGIONS: dict[int, list[list[float]]] = {
    1: [[0.17, 0.08, 0.37, 0.93]],  # pillar/doorway-frame beside the entrance glass door
}
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
MAX_FULL_RES_RECHECKS = config.RECOGNITION_MAX_FULL_RES_RECHECKS
RECHECK_CROP_PADDING = config.RECOGNITION_RECHECK_CROP_PADDING  # extra margin around a face's bbox, as a fraction of its own size
RECHECK_DET_THRESH = config.RECOGNITION_RECHECK_DET_THRESH  # lenient: an isolated single-face crop needs less context to detect than a full scene


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


def _lean_get(recognizer, img, input_size: tuple[int, int] | None = None):
    """Same as _lean_recognize_crop, but for every face FaceAnalysis.detect()
    finds in `img`, not just one crop — replaces recognizer._app.get() in the
    MAIN detection pass. FaceAnalysis.get()'s own source (confirmed by reading
    it directly) loops every non-detection model (landmark_3d_68,
    landmark_2d_106, genderage, recognition) over EVERY detected face; only
    recognition's output is ever read anywhere in this codebase. Measured live
    on the Technical section camera: a single main-pass call was taking ~10s
    end to end even for just 1-2 faces — this cuts that to roughly 1/4 by
    skipping the 3 models whose output nothing uses, with zero change to which
    face gets matched to whom.

    input_size overrides the detector's prepare()-time det_size for just this
    call (SCRFD.detect() accepts it directly — see CAMERA_DETECTION_MAX_DIM);
    None keeps the global default (640x640) other cameras use."""
    bboxes, kpss = recognizer._app.det_model.detect(img, max_num=0, metric="default", input_size=input_size)
    if bboxes.shape[0] == 0:
        return []
    faces = []
    for i in range(bboxes.shape[0]):
        kps = kpss[i] if kpss is not None else None
        face = Face(bbox=bboxes[i, 0:4], kps=kps, det_score=bboxes[i, 4])
        recognizer._app.models["recognition"].get(img, face)
        faces.append(face)
    return faces


def _detect_and_recognize(recognizer, frame, det_thresh: float, detection_max_dim: int) -> list[dict]:
    h, w = frame.shape[:2]
    scale = min(1.0, detection_max_dim / max(h, w))
    small = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1.0 else frame

    recognizer._app.det_model.det_thresh = det_thresh
    # small was already scaled to fit within detection_max_dim above, so this
    # input_size just tells the detector "don't shrink it again to the global
    # 640 default" — pairing the two is what makes the resolution bump real
    # (feeding a bigger image but letting the detector re-shrink it to 640
    # anyway would throw the extra detail straight back away).
    faces = _lean_get(recognizer, small, input_size=(detection_max_dim, detection_max_dim))
    for f in faces:
        f.bbox = f.bbox / scale
    faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)

    results = []
    rechecks_used = 0
    for f in faces:
        x1, y1, x2, y2 = [int(v) for v in f.bbox]
        name, score = recognizer._match(f.embedding)
        # Carried alongside name/score for footfall_counter.py's embedding-based
        # re-identification (unique people counting) — it needs the embedding
        # for EVERY face, enrolled or Unknown, not just recognition's name match.
        embedding = f.embedding

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
                        embedding = refined.embedding

        results.append({"bbox": [x1, y1, x2, y2], "name": name, "score": round(score, 3), "embedding": embedding})
    return results


def _save_fire_smoke_debug_frame(camera_id: int, frame, boxes: list[dict]) -> None:
    """Saves the exact analyzed frame (with the confirmed box(es) drawn on
    it) whenever a fire/smoke alert actually fires — see FIRE_SMOKE_DEBUG_DIR
    above. Best-effort only: a failure here should never take down the
    detect loop over a debug artifact."""
    try:
        FIRE_SMOKE_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        annotated = frame.copy()
        for box in boxes:
            x1, y1, x2, y2 = box["bbox"]
            color = (0, 0, 255) if box["type"] == "fire" else (200, 0, 150)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 3)
            cv2.putText(annotated, box["type"].upper(), (x1, max(20, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        types = "_".join(sorted({b["type"] for b in boxes})) or "unknown"
        path = FIRE_SMOKE_DEBUG_DIR / f"cam{camera_id}_{types}_{int(time.time())}.jpg"
        cv2.imwrite(str(path), annotated)
    except Exception:
        pass


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
    # Runs in its own OS process (see this module's docstring) — it never
    # inherits main.py's logging.basicConfig call, so without this, every
    # logger.* call below would silently go nowhere.
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )
    _cap_inference_threads(max_cpu_cores)

    # imported here, after the onnxruntime patch above is in place, so every
    # model these construct picks up the capped thread pool
    from .fire_smoke_detector import FireSmokeTracker
    from .person_tracker import PersonTracker
    from .pose_detector import PoseDetector
    from .recognizer import FaceRecognizer

    recognizer = FaceRecognizer()
    pose_detector = PoseDetector()
    trackers: dict[int, PersonTracker] = {}
    fire_smoke_trackers: dict[int, FireSmokeTracker] = {}
    last_pose_at: dict[int, float] = {}
    # fire_smoke_detector's events now keep firing every cycle for as long as
    # a condition stays confirmed (see FireSmokeTracker's docstring) so a
    # real, ongoing event keeps re-alerting instead of going silent after
    # one shot. Debug-frame saving needs its own throttle on top of that, or
    # a persisting event would write a new file to disk every ~1s forever.
    last_debug_save_at: dict[tuple[int, str], float] = {}

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
                original_thresh = recognizer._app.det_model.det_thresh
                recognizer._app.det_model.det_thresh = 0.3
                try:
                    faces = recognizer._app.get(frame)
                finally:
                    recognizer._app.det_model.det_thresh = original_thresh
                if faces:
                    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                    embedding = face.embedding
            embed_response_queue.put({"request_id": item["request_id"], "embedding": embedding})
            continue

        if kind == "detect":
            camera_id = item["camera_id"]
            received_at = time.time()
            # queue_wait_ms: time this frame sat in the worker's input queue
            # before being picked up — a growing value here (not processing
            # duration) is the signature of a camera being starved by its own
            # or another camera's backlog, distinct from recognition itself
            # being slow. enqueued_at is set by pipeline.py's _sender_loop.
            queue_wait_ms = round((received_at - item.get("enqueued_at", received_at)) * 1000, 1)
            frame = cv2.imdecode(np.frombuffer(item["jpeg"], np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                logger.warning("camera=%s stage=frame_decode_failed queue_wait_ms=%s", camera_id, queue_wait_ms)
                continue
            logger.debug("camera=%s stage=frame_received queue_wait_ms=%s", camera_id, queue_wait_ms)

            det_thresh = CAMERA_DET_THRESH.get(camera_id, DEFAULT_DET_THRESH)
            detection_max_dim = CAMERA_DETECTION_MAX_DIM.get(camera_id, DETECTION_DOWNSCALE_MAX_DIM)
            recognize_start = time.time()
            faces = _detect_and_recognize(recognizer, frame, det_thresh, detection_max_dim)
            recognize_ms = round((time.time() - recognize_start) * 1000, 1)
            result = {
                "camera_id": camera_id,
                "faces": faces,
                # Pixel size of THIS frame — desk_tracker.py needs it to turn
                # a face bbox back into the 0..1 fraction desk zones are
                # stored in; cheap (two ints) so always included rather than
                # gated behind whether any camera currently has zones.
                "frame_size": [frame.shape[1], frame.shape[0]],
            }
            if faces:
                for f in faces:
                    logger.debug(
                        "camera=%s stage=recognized name=%s score=%s bbox=%s recognize_ms=%s",
                        camera_id, f["name"], f["score"], f["bbox"], recognize_ms,
                    )
            else:
                logger.debug("camera=%s stage=no_faces_detected recognize_ms=%s", camera_id, recognize_ms)

            # Cheap (no NN) — runs every detect cycle rather than being
            # gated to POSE_INTERVAL_SECONDS, so a real fire/smoke event
            # gets caught as fast as face recognition does. Every already
            # -detected face is excluded from consideration (see
            # fire_smoke_detector.FACE_EXCLUDE_*_PAD) — a person moving at
            # their desk otherwise reads as a "growing" smoke-colored blob.
            fs_tracker = fire_smoke_trackers.setdefault(camera_id, FireSmokeTracker())
            face_boxes = [f["bbox"] for f in result["faces"]]
            frame_h, frame_w = frame.shape[:2]
            ignore_boxes = [
                [int(x1 * frame_w), int(y1 * frame_h), int(x2 * frame_w), int(y2 * frame_h)]
                for x1, y1, x2, y2 in CAMERA_FIRE_SMOKE_IGNORE_REGIONS.get(camera_id, [])
            ]
            fire_smoke = fs_tracker.update(frame, exclude_boxes=face_boxes + ignore_boxes)
            result["fire_smoke"] = fire_smoke["boxes"]
            result["fire_smoke_events"] = fire_smoke["events"]
            for event_type in fire_smoke["events"]:
                debug_key = (camera_id, event_type)
                if time.time() - last_debug_save_at.get(debug_key, 0) >= FIRE_SMOKE_DEBUG_SAVE_INTERVAL_SECONDS:
                    last_debug_save_at[debug_key] = time.time()
                    _save_fire_smoke_debug_frame(camera_id, frame, fire_smoke["boxes"])

            now = time.time()
            if now - last_pose_at.get(camera_id, 0) >= POSE_INTERVAL_SECONDS:
                last_pose_at[camera_id] = now
                people = pose_detector.detect(frame)
                tracker = trackers.setdefault(camera_id, PersonTracker())
                events = tracker.update(people, frame.shape[0])
                result["footfall_events"] = events["footfall_events"]
                result["fall_events"] = events["fall_events"]
                result["person_count"] = len(people)
                # Bboxes only (no keypoints — desk_tracker.py just needs
                # "is a body inside this zone", not pose) for desk-time
                # analytics' pose-based continuity bridge. Computed above
                # regardless; previously discarded once footfall/fall
                # events were derived from it.
                result["people"] = [{"bbox": p["bbox"]} for p in people]

            try:
                result_queue.put_nowait(result)
            except queue.Full:
                pass
