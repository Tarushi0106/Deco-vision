"""Starts/stops a CameraWorker per camera Parachute reports as live, kept
in sync with Parachute's own camera list on a timer — a camera added,
removed, or toggled off in Parachute is picked up here automatically,
without this service needing any push notification or direct DB access.
"""

import logging
import threading
import time

from . import config, parachute_client
from .camera_worker import CameraWorker
from .recognizer import get_recognizer

logger = logging.getLogger("detection.manager")


class DetectionManager:
    def __init__(self):
        self._workers: dict[int, CameraWorker] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        get_recognizer().sync_roster_from_parachute(force=True)
        self._refresh_cameras()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        with self._lock:
            for worker in self._workers.values():
                worker.stop()
            self._workers.clear()

    def _refresh_cameras(self) -> None:
        cameras = parachute_client.list_cameras()
        live_ids = {c["id"] for c in cameras if c.get("live")}
        with self._lock:
            for camera_id in list(self._workers):
                if camera_id not in live_ids:
                    self._workers.pop(camera_id).stop()
                    logger.info("Camera %s: no longer live in Parachute, stopped", camera_id)
            for cam in cameras:
                if cam["id"] in live_ids and cam["id"] not in self._workers:
                    worker = CameraWorker(cam["id"], cam.get("name", str(cam["id"])))
                    worker.start()
                    self._workers[cam["id"]] = worker
                    logger.info("Camera %s (%s): worker started", cam["id"], cam.get("name"))

    def _loop(self) -> None:
        last_camera_refresh = 0.0
        last_roster_refresh = 0.0
        while self._running:
            now = time.time()
            if now - last_camera_refresh >= config.CAMERA_LIST_REFRESH_SECONDS:
                last_camera_refresh = now
                try:
                    self._refresh_cameras()
                except Exception:
                    logger.exception("Failed to refresh camera list from Parachute")
            if now - last_roster_refresh >= config.ROSTER_REFRESH_SECONDS:
                last_roster_refresh = now
                try:
                    get_recognizer().sync_roster_from_parachute()
                except Exception:
                    logger.exception("Failed to refresh roster from Parachute")
            time.sleep(2)

    def live_camera_ids(self) -> list[int]:
        with self._lock:
            return sorted(self._workers.keys())

    def get_live_detections(self, camera_id: int) -> dict | None:
        with self._lock:
            worker = self._workers.get(camera_id)
        return worker.get_live_detections() if worker else None

    def is_live(self, camera_id: int) -> bool:
        with self._lock:
            return camera_id in self._workers


detection_manager = DetectionManager()
