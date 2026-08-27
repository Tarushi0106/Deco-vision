"""Background catch-up fetcher for cameras using ONVIF replay caching
(config.CAMERA_ONVIF_REPLAY_CHANNEL) - proactively pulls any clip logged
without a cached file yet, instead of waiting for someone to click Play, so
the rolling config.CLIP_RETENTION_DAYS window is actually populated ahead
of time rather than only on demand.

Bounded by what the camera's OWN onboard recording can still produce: a
fetch for a clip whose window has already rolled off the camera's storage
comes back with no stream data at all (confirmed live - see
onvif_client.py's module docstring). Retrying that forever would waste a
full fetch-timeout on every single pass, so a failed fetch is written as
clips_db.UNAVAILABLE_SENTINEL and never attempted again - same convention
main.py's on-demand endpoint uses, so a settled miss behaves identically
whichever path reached it first.

Runs on its own thread with modest concurrency: each fetch is ~2-3 minutes
of mostly network/camera-side wait (see onvif_client.py's measured latency
model), not CPU-bound, so overlapping a couple of them shortens the backlog
without meaningfully competing with the live capture threads or the
detection worker for CPU.
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import camera_db, clips_db, config, onvif_client

logger = logging.getLogger("dashboard.replay_prefetch")

POLL_INTERVAL_SECONDS = 30.0  # how often to look for newly-logged, not-yet-cached clips once the backlog is empty
MAX_CONCURRENT_FETCHES = 2
BATCH_SIZE = MAX_CONCURRENT_FETCHES

_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _fetch_one(clip: dict) -> None:
    replay_channel = config.CAMERA_ONVIF_REPLAY_CHANNEL.get(clip["camera_id"])
    cam = camera_db.get_camera_connection(clip["camera_id"])
    if replay_channel is None or cam is None:
        return

    replay_url = onvif_client.build_replay_rtsp_url(
        cam["host"], cam["port"], cam["user"], cam["password"], replay_channel,
        clip["ts"] - onvif_client.REPLAY_PADDING_SECONDS,
        clip["ts"] + clip["duration"] + onvif_client.REPLAY_PADDING_SECONDS,
    )
    # Same generous fixed-floor timeout model as main.py's on-demand fetch —
    # see that endpoint's comment for the live-measured reasoning.
    timeout = clip["duration"] * 1.5 + 180.0
    camera_dir = clips_db.CLIPS_DIR / str(clip["camera_id"])
    camera_dir.mkdir(parents=True, exist_ok=True)
    out_path = camera_dir / f"replay_{clip['id']}_{uuid.uuid4().hex[:8]}.mp4"

    fetched = onvif_client.fetch_replay_clip(replay_url, out_path, timeout=timeout)
    if fetched is None:
        clips_db.set_clip_file_path(clip["id"], clips_db.UNAVAILABLE_SENTINEL)
        logger.info("Replay prefetch: clip %s no longer available on camera %s", clip["id"], clip["camera_id"])
    else:
        clips_db.set_clip_file_path(clip["id"], str(fetched))
        logger.info("Replay prefetch: cached clip %s (camera %s)", clip["id"], clip["camera_id"])


def _run() -> None:
    camera_ids = list(config.CAMERA_ONVIF_REPLAY_CHANNEL)
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_FETCHES) as pool:
        while not _stop_event.is_set():
            pending = clips_db.list_pending_replay_clips(camera_ids, config.CLIP_RETENTION_DAYS, BATCH_SIZE)
            if not pending:
                _stop_event.wait(POLL_INTERVAL_SECONDS)
                continue
            # list() drains the map so this pass waits for the batch to
            # finish (successes and settled misses alike) before re-querying
            # for the next one — otherwise an empty query could re-launch
            # fetches for clips already in flight.
            list(pool.map(_fetch_one, pending))


def start() -> None:
    global _thread
    if _thread is not None or not config.CAMERA_ONVIF_REPLAY_CHANNEL:
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_run, name="replay-prefetch", daemon=True)
    _thread.start()
    logger.info("Replay prefetch worker started (cameras: %s)", list(config.CAMERA_ONVIF_REPLAY_CHANNEL))


def stop() -> None:
    global _thread
    _stop_event.set()
    _thread = None
