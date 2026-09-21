"""Background retraining trigger — its own daemon thread, checked on a
timer. Never runs inside a request handler, never blocks the camera
workers or the API. A plain threading.Thread + sleep loop is enough here
(no APScheduler dependency needed for one repeating interval job, unlike
Parachute's own scheduler.py which also handles cron-style daily jobs)."""

import logging
import threading
import time

from . import config, face_training_db, training

logger = logging.getLogger("detection.scheduler")

_thread: threading.Thread | None = None
_stop = threading.Event()


def _check_once() -> None:
    candidates = face_training_db.get_labeled_awaiting_promotion()
    if not candidates:
        return
    last_run = face_training_db.get_latest_training_run()
    time_since_last = time.time() - last_run["trained_at"] if last_run else float("inf")
    should_run = (
        len(candidates) >= config.FACE_TRAINING_MIN_NEW_SAMPLES
        or time_since_last >= config.FACE_TRAINING_MAX_INTERVAL_SECONDS
    )
    if should_run:
        training.run_promotion_cycle(min_samples=1)


def _loop() -> None:
    while not _stop.is_set():
        _stop.wait(config.FACE_TRAINING_CHECK_INTERVAL_SECONDS)
        if _stop.is_set():
            break
        try:
            _check_once()
        except Exception:
            logger.exception("face-training: scheduled promotion cycle failed")


def start() -> None:
    global _thread
    if _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
    logger.info(
        "face-training: background promotion check every %.0fs (promotes at >=%d new labeled samples or %.0fs elapsed)",
        config.FACE_TRAINING_CHECK_INTERVAL_SECONDS, config.FACE_TRAINING_MIN_NEW_SAMPLES,
        config.FACE_TRAINING_MAX_INTERVAL_SECONDS,
    )


def stop() -> None:
    global _thread
    _stop.set()
    _thread = None
