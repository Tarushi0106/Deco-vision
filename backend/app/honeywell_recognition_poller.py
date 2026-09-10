"""Identity source of truth for Attendance/People Analytics: the Honeywell
camera's OWN onboard face-recognition log (SnapedFaces, via
recognition_provider.HoneywellRecognitionProvider), not local ArcFace
matching. The local per-frame pipeline keeps running for desk/footfall/gate
tracking and clip-session continuity, which still need a real-time bbox
stream this asynchronous event log can't provide; it just no longer decides
what name gets shown for a recognized person.

Architecture (redesigned from the original single-loop version to survive
restarts/outages without losing or duplicating events — see the project plan
this was built against for the full rationale):

- One polling thread per PHYSICAL DEVICE (host), not per `cameras` row.
  Two rows can share one host (different RTSP channels off one Allow List);
  they still serialize through camera_client.CameraClient's own per-device
  lock, but a permanently-dead host no longer blocks a live host's polling —
  under the old single-shared-loop design, one camera's connection timeout
  delayed every other camera's poll that same cycle.
- A lightweight manager thread reconciles that per-host thread set against
  camera_db (so cameras added/removed via Camera Management still take
  effect without a backend restart, matching the old behavior) and restarts
  a host's thread if it ever dies unexpectedly (mirrors PipelineManager's
  worker health-check/restart pattern).
- Each camera row's read position is checkpointed to
  face_db.honeywell_poll_checkpoints after every successful poll, not just
  held in memory, so a restart resumes from where it left off instead of a
  fixed short lookback.
- Each written event carries a deterministic event_key
  (face_db.insert_detection_event_if_new, backed by a UNIQUE index) so the
  same recognition occurrence can never be written twice even across a
  restart — this replaces the old in-memory-only de-dup set.
- Person-ID -> name resolution goes through an in-memory cache built from
  our already-synced local People List (face_db.get_faces_by_camera_host),
  refreshed on a timer and immediately on a cache miss — not a live camera
  API call per event.
- A per-host failing connection backs off exponentially (capped), instead
  of being retried every fixed interval regardless of repeated failure.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta

from . import camera_db, config, face_db
from .recognition_provider import HoneywellRecognitionProvider

logger = logging.getLogger("dashboard.honeywell_recognition")

_provider = HoneywellRecognitionProvider()

# How far back to look on a camera row's very first poll ever (no persisted
# checkpoint yet) — short, since this is meant to catch up on the last few
# seconds of log entries written while the poller was starting up, not
# backfill history.
_INITIAL_LOOKBACK_SECONDS = 10

# How often the manager thread re-checks camera_db for added/removed cameras
# and restarts any host-thread that died unexpectedly. Independent of (and
# much shorter than) each host's own HONEYWELL_POLL_INTERVAL_SECONDS.
_HOST_RECONCILE_INTERVAL_SECONDS = 15

_STRANGER_NAMES = {"", "stranger", "unknown"}

_stop_event = threading.Event()
_manager_thread: threading.Thread | None = None
_host_threads: dict[str, threading.Thread] = {}
_host_stop_events: dict[str, threading.Event] = {}
_host_cameras: dict[str, list[dict]] = {}

# Per-camera-row read position — mirrored to face_db so it survives a
# restart; kept in memory too so a live process doesn't re-hit the DB every
# single tick just to read back what it itself wrote last tick.
_last_polled_until: dict[int, datetime] = {}

# Same (camera_id, identity) write-throttle the original poller had —
# distinct from event_key de-dup (which stops the SAME Honeywell occurrence
# being written twice) and from PRESENCE_GRACE_SECONDS (which groups raw
# sightings into attendance sessions, see face_db.get_person_day_sessions).
# This one just limits how often we write genuinely-new raw sightings for a
# continuously-present person.
_last_logged_at: dict[tuple[int, str], float] = {}

# In-memory People List cache: camera_face_id ("{host}:{Id}") -> name.
_person_cache: dict[str, str] = {}
_person_cache_lock = threading.Lock()
_person_cache_loaded_at = 0.0
_unknown_id_seen: dict[str, int] = {}

# Per-host health, read by main.py's stats endpoint (get_health_status()).
_host_health: dict[str, dict] = {}
_events_processed_today: dict[str, int] = {}
_events_processed_day: str | None = None


def _channel_for(stream_path: str) -> str:
    """"/h264/ch2/sub/av_stream" -> "CH2" (recognition_provider's channel
    param) — defaults to CH1 if the path doesn't encode one."""
    match = re.search(r"ch(\d+)", stream_path or "", re.IGNORECASE)
    return f"CH{match.group(1)}" if match else "CH1"


def _eligible_cameras() -> list[dict]:
    """camera_db.list_cameras() strips the password (never sent to the
    frontend) — re-fetch each candidate row via get_camera_connection() for
    the real credentials this module needs to call the camera's admin API."""
    candidates = [
        cam for cam in camera_db.list_cameras()
        if cam.get("status") == "active" and cam.get("host") and cam.get("user")
    ]
    cameras = (camera_db.get_camera_connection(cam["id"]) for cam in candidates)
    return [cam for cam in cameras if cam and cam.get("password")]


def _load_person_cache(force: bool = False) -> None:
    global _person_cache_loaded_at
    now = time.time()
    if not force and (now - _person_cache_loaded_at) < config.HONEYWELL_PEOPLE_CACHE_REFRESH_INTERVAL_SECONDS:
        return
    hosts = {cam["host"] for cam in _eligible_cameras()}
    fresh: dict[str, str] = {}
    for host in hosts:
        for row in face_db.get_faces_by_camera_host(host):
            if row.get("camera_face_id"):
                fresh[row["camera_face_id"]] = row["name"]
    with _person_cache_lock:
        _person_cache.clear()
        _person_cache.update(fresh)
        _person_cache_loaded_at = now
    logger.debug("stage=people_cache_refreshed entries=%d hosts=%s", len(fresh), sorted(hosts))


def _lookup_person(camera_face_id: str) -> str | None:
    _load_person_cache()  # cheap no-op unless the refresh interval has elapsed
    with _person_cache_lock:
        return _person_cache.get(camera_face_id)


def _bump_events_today(host: str) -> None:
    global _events_processed_day
    today = datetime.now().strftime("%Y-%m-%d")
    if today != _events_processed_day:
        _events_processed_today.clear()
        _events_processed_day = today
    _events_processed_today[host] = _events_processed_today.get(host, 0) + 1


def _process_event(camera_id: int, host: str, channel: str, event, fetched_at: float) -> None:
    if isinstance(event.name, str) and event.name.strip().lower() in _STRANGER_NAMES and event.person_id is None:
        return  # nothing to resolve and no ID either — not a real recognition

    resolved_name = event.name
    if event.person_id is not None:
        camera_face_id = f"{host}:{event.person_id}"
        cached_name = _lookup_person(camera_face_id)
        if cached_name is None:
            _unknown_id_seen[camera_face_id] = _unknown_id_seen.get(camera_face_id, 0) + 1
            logger.warning(
                "camera=%s stage=unknown_person_id person_id=%s not in local People List cache — refreshing",
                camera_id, event.person_id,
            )
            _load_person_cache(force=True)
            cached_name = _lookup_person(camera_face_id)
        if cached_name is not None:
            resolved_name = cached_name
            logger.debug(
                "camera=%s stage=id_resolved person_id=%s (raw_name=%r) -> resolved_name=%r via People List",
                camera_id, event.person_id, event.name, resolved_name,
            )
        else:
            logger.warning(
                "camera=%s stage=id_unresolved person_id=%s still unknown after cache refresh — "
                "storing as an unresolved recognition event rather than discarding it",
                camera_id, event.person_id,
            )
            resolved_name = None

    if resolved_name is None and event.person_id is None:
        return  # nothing usable at all

    # Keyed on resolution status, not just person_id: an earlier UNRESOLVED
    # sighting of this ID must not suppress the next sighting that resolves
    # correctly (e.g. once the people cache catches up) — that would delay a
    # person's real name reaching detection_events by up to a full cooldown
    # window for no benefit. Unresolved sightings of the same ID still
    # throttle each other, just in their own bucket.
    dedup_bucket = (camera_id, resolved_name if resolved_name is not None else f"unresolved:{event.person_id}")
    last_logged = _last_logged_at.get(dedup_bucket)
    now = time.time()
    if last_logged and (now - last_logged) < config.DETECTION_LOG_COOLDOWN_SECONDS:
        return

    if event.raw_event_id:
        event_key = f"real:{event.raw_event_id}"
    else:
        identity_key = event.person_id or resolved_name
        event_key = f"composite:{identity_key}:{event.event_ts}:{channel}"

    recognition_source = "honeywell"
    if resolved_name is None:
        recognition_source = "unresolved"
    elif (
        config.HONEYWELL_LOW_CONFIDENCE_THRESHOLD is not None
        and event.score is not None
        and event.score < config.HONEYWELL_LOW_CONFIDENCE_THRESHOLD
    ):
        recognition_source = "low_confidence"

    resolved_at = time.time()
    inserted = face_db.insert_detection_event_if_new(
        camera_id, resolved_name, bbox=[], event_key=event_key, score=event.score,
        honeywell_person_id=event.person_id, honeywell_event_ts=event.event_ts,
        recognition_source=recognition_source,
    )
    written_at = time.time()

    if not inserted:
        logger.debug("camera=%s stage=duplicate_skipped event_key=%s", camera_id, event_key)
        return

    _last_logged_at[dedup_bucket] = now
    _bump_events_today(host)

    event_to_write_ms = f"{(written_at - event.event_ts) * 1000:.1f}" if event.event_ts else "unknown"
    logger.info(
        "Honeywell Camera (camera_id=%s) -> Recognition Event -> person_id=%s -> person_name=%r "
        "(score=%s, source=%s) -> DECO Vision Backend logged -> Attendance/People Analytics/Dashboard updated "
        "| latency fetched_at=%.3f resolved_at=%.3f written_at=%.3f "
        "fetch_to_write_ms=%.1f honeywell_event_to_write_ms=%s",
        camera_id, event.person_id, resolved_name, event.score, recognition_source,
        fetched_at, resolved_at, written_at, (written_at - fetched_at) * 1000, event_to_write_ms,
    )


def _poll_camera_row(cam: dict) -> None:
    camera_id = cam["id"]
    host = cam["host"]
    now = datetime.now()

    since = _last_polled_until.get(camera_id)
    if since is None:
        saved = face_db.get_poll_checkpoint(camera_id)
        if saved and saved.get("last_processed_ts"):
            since = datetime.fromtimestamp(saved["last_processed_ts"])
            logger.info("camera=%s stage=checkpoint_loaded resuming from %s (persisted)", camera_id, since)
        else:
            since = now - timedelta(seconds=_INITIAL_LOOKBACK_SECONDS)
            logger.info(
                "camera=%s stage=checkpoint_new no persisted checkpoint — starting from %ss lookback",
                camera_id, _INITIAL_LOOKBACK_SECONDS,
            )

    channel = _channel_for(cam.get("stream_path", ""))
    logger.debug("camera=%s host=%s channel=%s stage=connected fetching recognition events since %s", camera_id, host, channel, since)
    fetched_at = time.time()
    events = _provider.fetch_new_events(
        host, cam["user"], cam["password"], cam.get("admin_port") or config.CAMERA_ADMIN_PORT,
        channel, since, now,
    )
    if events:
        logger.info("camera=%s stage=events_received received %d recognition event(s) from Honeywell Camera", camera_id, len(events))
    else:
        logger.debug("camera=%s stage=poll channel=%s window=%s..%s entries=0", camera_id, channel, since, now)

    for event in events:
        _process_event(camera_id, host, channel, event, fetched_at)

    _last_polled_until[camera_id] = now
    face_db.save_poll_checkpoint(camera_id, host, now.timestamp(), None)


def _host_loop(host: str, stop_evt: threading.Event) -> None:
    delay = config.HONEYWELL_RECONNECT_BASE_DELAY_SECONDS
    while not stop_evt.is_set() and not _stop_event.is_set():
        cameras = _host_cameras.get(host, [])
        success = True
        for cam in cameras:
            try:
                _poll_camera_row(cam)
            except Exception:
                logger.exception("camera=%s host=%s stage=poll_failed", cam.get("id"), host)
                success = False

        _host_health[host] = {
            "last_poll_at": time.time(),
            "healthy": success,
            "backoff_delay_seconds": 0 if success else delay,
            "events_processed_today": _events_processed_today.get(host, 0),
            "unresolved_ids": sum(v for k, v in _unknown_id_seen.items() if k.startswith(f"{host}:")),
        }

        if success:
            delay = config.HONEYWELL_RECONNECT_BASE_DELAY_SECONDS
            wait = config.HONEYWELL_POLL_INTERVAL_SECONDS
        else:
            wait = delay
            delay = min(delay * 2, config.HONEYWELL_RECONNECT_MAX_DELAY_SECONDS)
            logger.warning("host=%s stage=backoff next attempt in %ss", host, wait)
        stop_evt.wait(wait)


def _run_manager() -> None:
    """Reconciles one polling thread per currently-eligible physical host —
    same "diff the desired set against the running set" pattern
    PipelineManager uses for per-camera worker processes, applied to
    threads. Also restarts a host's thread if it ever dies unexpectedly."""
    while not _stop_event.is_set():
        by_host: dict[str, list[dict]] = {}
        for cam in _eligible_cameras():
            by_host.setdefault(cam["host"], []).append(cam)

        for host, cameras in by_host.items():
            _host_cameras[host] = cameras
            thread = _host_threads.get(host)
            if thread is None or not thread.is_alive():
                if thread is not None:
                    logger.error("host=%s stage=thread_died restarting polling thread", host)
                stop_evt = threading.Event()
                _host_stop_events[host] = stop_evt
                new_thread = threading.Thread(target=_host_loop, args=(host, stop_evt), name=f"honeywell-poll-{host}", daemon=True)
                _host_threads[host] = new_thread
                new_thread.start()
                logger.info("host=%s stage=thread_started cameras=%s", host, [c["id"] for c in cameras])

        for host in list(_host_threads):
            if host not in by_host:
                _host_stop_events[host].set()
                del _host_threads[host]
                del _host_stop_events[host]
                _host_cameras.pop(host, None)
                _host_health.pop(host, None)
                logger.info("host=%s stage=thread_stopped (no longer an eligible active camera host)", host)

        _stop_event.wait(_HOST_RECONCILE_INTERVAL_SECONDS)

    for stop_evt in _host_stop_events.values():
        stop_evt.set()
    for thread in _host_threads.values():
        thread.join(timeout=5)


def get_health_status() -> dict:
    """Per-host health snapshot for main.py's stats endpoint — lets the
    dashboard distinguish "no one has walked by" from "the recognition
    pipeline is broken"."""
    return {
        host: {
            **health,
            "worker_running": host in _host_threads and _host_threads[host].is_alive(),
        }
        for host, health in _host_health.items()
    }


def start() -> None:
    global _manager_thread
    _stop_event.clear()
    _load_person_cache(force=True)
    _manager_thread = threading.Thread(target=_run_manager, name="honeywell-recognition-manager", daemon=True)
    _manager_thread.start()
    logger.info(
        "Honeywell recognition poller started (poll_interval=%ss, per-host threads, "
        "reconnect_backoff=%s-%ss)",
        config.HONEYWELL_POLL_INTERVAL_SECONDS,
        config.HONEYWELL_RECONNECT_BASE_DELAY_SECONDS, config.HONEYWELL_RECONNECT_MAX_DELAY_SECONDS,
    )


def stop() -> None:
    _stop_event.set()
    for stop_evt in _host_stop_events.values():
        stop_evt.set()
    if _manager_thread is not None:
        _manager_thread.join(timeout=10)
