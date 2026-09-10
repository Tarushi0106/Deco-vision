"""Talks to the Honeywell camera's own admin REST API (the same one its
web UI uses) to push enrolled people into its onboard face database
(Allow List), so the camera's own recognition picks them up immediately
— not just our dashboard's local model.
"""

import base64
import logging
import threading
import time

import requests
import urllib3
from requests.auth import HTTPDigestAuth

from . import camera_db, config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("dashboard.camera_client")

ALLOW_LIST_GROUP_ID = 2

# This device has been diagnosed (diagnose_honeywell.py, run repeatedly from
# both the local machine and AWS) to reliably serve roughly one request per
# connection before resetting it — so a second failure right after a fresh
# relogin is an expected outcome under real device instability, not a client
# bug to retry harder against. Bounded at 2 total attempts (1 relogin) rather
# than higher: retrying more would turn a device that's already struggling
# under one request into a request storm, exactly what section 2 of the task
# warns against, without meaningfully improving the odds of success.
MAX_REQUEST_ATTEMPTS = int(getattr(config, "HONEYWELL_MAX_REQUEST_ATTEMPTS", 2))


def _classify_exception(e: Exception) -> str:
    """Mirrors scripts/diagnose_honeywell.py's categorization so production
    logs and the standalone diagnostic never disagree about what a given
    failure actually was — the one thing this integration must never do is
    collapse "device reset the connection" and "genuinely no data" into the
    same bucket."""
    msg = str(e)
    if isinstance(e, requests.exceptions.ConnectTimeout) or ("Connection to" in msg and "timed out" in msg):
        return "TIMEOUT_CONNECT"
    if isinstance(e, requests.exceptions.ReadTimeout):
        return "TIMEOUT_READ"
    if isinstance(e, requests.exceptions.ConnectionError):
        if "actively refused" in msg or "10061" in msg:
            return "CONNECTION_REFUSED"
        if "forcibly closed" in msg or "Connection aborted" in msg or "RemoteDisconnected" in msg:
            return "CONNECTION_RESET"
        return "CONNECTION_ERROR"
    if isinstance(e, requests.exceptions.HTTPError):
        if getattr(e, "response", None) is not None and e.response.status_code == 401:
            return "AUTH_FAILED"
        return "HTTP_ERROR"
    if isinstance(e, ValueError):  # resp.json() raises this (JSONDecodeError subclasses it) on a malformed body
        return "MALFORMED_RESPONSE"
    return f"UNEXPECTED_ERROR({type(e).__name__})"


class _Stats:
    """Per-device counters for main.py's /api/stats — lets the dashboard (and
    a human staring at it) immediately tell CAMERA/API/NETWORK trouble apart
    from "it's just quiet right now". Deliberately plain counters, not a
    metrics library: this integration talks to exactly one flaky device at a
    time, not a fleet needing real aggregation."""

    def __init__(self):
        self.login_success = 0
        self.login_fail = 0
        self.reconnect_attempts = 0
        self.reconnect_success = 0
        self.reconnect_fail = 0
        self.request_success = 0
        self.request_empty = 0
        self.request_timeout = 0
        self.request_reset = 0
        self.request_refused = 0
        self.request_http_error = 0
        self.request_auth_failed = 0
        self.request_malformed = 0
        self.request_other_error = 0
        self.last_login_at: float | None = None
        self.last_successful_request_at: float | None = None

    def record_failure(self, category: str) -> None:
        attr = {
            "TIMEOUT_CONNECT": "request_timeout", "TIMEOUT_READ": "request_timeout",
            "CONNECTION_RESET": "request_reset", "CONNECTION_REFUSED": "request_refused",
            "CONNECTION_ERROR": "request_other_error", "HTTP_ERROR": "request_http_error",
            "AUTH_FAILED": "request_auth_failed", "MALFORMED_RESPONSE": "request_malformed",
        }.get(category, "request_other_error")
        setattr(self, attr, getattr(self, attr) + 1)

    def as_dict(self) -> dict:
        return {k: v for k, v in vars(self).items()}


class CameraClient:
    def __init__(self, host: str, user: str, password: str, port: int = config.CAMERA_ADMIN_PORT):
        self._base = f"https://{host}" if port == 443 else f"https://{host}:{port}"
        self._user = user
        self._password = password
        self._session = requests.Session()
        self._session.verify = False
        self._logged_in = False
        self.stats = _Stats()
        # One physical device can back several `cameras` rows (different RTSP
        # channels), each polled independently by honeywell_recognition_poller
        # on its own thread, plus on-demand calls like people/sync-from-camera
        # — all sharing this one CameraClient/session per host:port. The
        # device requires Search immediately before GetByIndex in the same
        # session; two of those sequences interleaving on the shared session
        # (e.g. camera row 2's poll and camera row 3's poll landing at the
        # same time) has been observed producing a false Count=0 rather than
        # an error. This lock serializes every call through one client so
        # only one full request sequence is ever in flight against a device.
        self._lock = threading.Lock()

    def _login(self, is_reconnect: bool = False) -> bool:
        """Returns True/False rather than raising — a login failure during a
        _post retry sequence is a normal, expected outcome of this device's
        documented instability, not something the caller should have to
        catch a fresh exception type for. is_reconnect only changes which
        counters/log line this attempt is credited to (LOGIN vs RECONNECT,
        per the task's requested log shape), not the behavior."""
        # This client is a long-lived singleton per host:port (reused across
        # hours of reconnects under this device's documented instability) —
        # clear any cookie carried forward from a much earlier login before
        # authenticating fresh. A stale cookie from a session the device has
        # since closed/expired, sent alongside a brand-new CSRF token, is a
        # plausible explanation for AddedFaces/Search intermittently
        # returning a technically-200-OK but logically-empty Count=0 even
        # right after a successful re-login (observed repeatedly this
        # session) — this makes every login start from a genuinely clean
        # session state rather than accumulating one across the client's
        # entire process lifetime.
        self._session.cookies.clear()
        if is_reconnect:
            self.stats.reconnect_attempts += 1
        try:
            resp = self._session.post(
                f"{self._base}/API/Web/Login",
                auth=HTTPDigestAuth(self._user, self._password),
                json={"data": {"support_new_schedule": True, "remote_terminal_info": "WEB,chrome"}},
                verify=False,
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as e:
            category = _classify_exception(e)
            self.stats.login_fail += 1
            if is_reconnect:
                self.stats.reconnect_fail += 1
            logger.warning(
                "Honeywell: %s -> FAIL (%s) host=%s error=%s",
                "RECONNECT" if is_reconnect else "LOGIN", category, self._base, e,
            )
            return False
        # required on every subsequent request, or the server resets the connection
        csrf_token = resp.headers.get("X-csrftoken")
        if csrf_token:
            self._session.headers.update({"X-csrftoken": csrf_token})
        self._logged_in = True
        self.stats.login_success += 1
        self.stats.last_login_at = time.time()
        if is_reconnect:
            self.stats.reconnect_success += 1
        logger.info("Honeywell: %s -> SUCCESS host=%s", "RECONNECT" if is_reconnect else "LOGIN", self._base)
        return True

    def _post(self, path: str, data: dict) -> dict:
        # Callers hold self._lock for their whole request sequence (see
        # list_added_faces/search_snaped_faces/etc.) — not acquired here,
        # since a sequence like Search-then-GetByIndex must stay uninterrupted
        # across both calls, not just within each individual POST.
        if not self._logged_in and not self._login():
            raise requests.exceptions.ConnectionError(f"Honeywell login failed for {self._base}")

        last_exc: Exception | None = None
        for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
            try:
                resp = self._session.post(
                    f"{self._base}{path}", json={"version": "1.0", "data": data}, verify=False, timeout=20,
                )
                resp.raise_for_status()
                try:
                    body = resp.json()
                except ValueError:
                    # A 200 with an unparseable body is exactly the "malformed
                    # response" case the task calls out separately from an
                    # empty-but-valid result — must never be silently treated
                    # as "0 people"/"no events". Extra detail (body_head) logged
                    # here since only this scope has `resp`; the actual stats
                    # increment happens once, in the except block below, same
                    # as every other failure category — not here too, or a
                    # malformed response spanning a retry would double-count.
                    logger.error(
                        "Honeywell: REQUEST -> MALFORMED_RESPONSE path=%s status=%s body_head=%r",
                        path, resp.status_code, resp.text[:200],
                    )
                    raise
                self.stats.request_success += 1
                self.stats.last_successful_request_at = time.time()
                logger.debug("Honeywell: REQUEST -> SUCCESS path=%s status=%s attempt=%d", path, resp.status_code, attempt)
                return body
            except Exception as e:
                category = _classify_exception(e)
                self.stats.record_failure(category)
                last_exc = e
                logger.warning(
                    "Honeywell: REQUEST -> %s path=%s attempt=%d/%d error=%s",
                    category, path, attempt, MAX_REQUEST_ATTEMPTS, e,
                )
                if attempt >= MAX_REQUEST_ATTEMPTS:
                    break
                # One bounded reconnect-and-retry — not a loop, and never more
                # than MAX_REQUEST_ATTEMPTS total HTTP calls for this one
                # logical request, so a persistently-down device degrades to
                # "fail fast, let the poller's own backoff decide when to try
                # again" rather than hammering it.
                self._logged_in = False
                if not self._login(is_reconnect=True):
                    break  # reconnect itself failed — retrying the request would be doomed too
        raise last_exc

    def search_snaped_faces(self, start_time: str, end_time: str, channel: str = "CH1", count: int = 20) -> dict:
        """Onboard face-recognition log: every face the camera has captured,
        matched against the Allow List with identity — the same engine behind
        its "Face Detection alarm!" events."""
        with self._lock:
            self._post(
                "/API/AI/SnapedFaces/Search",
                {
                    "MsgId": "",
                    "StartTime": start_time,
                    "EndTime": end_time,
                    "Chn": [channel],
                    "AlarmGroup": [],
                    "Similarity": 0,
                    "Engine": 1,
                    "Count": 0,
                    "FaceInfo": [],
                },
            )
            return self._post(
                "/API/AI/SnapedFaces/GetByIndex",
                {
                    "MsgId": "",
                    "Engine": 1,
                    "MatchedFaces": 0,
                    "StartIndex": 0,
                    "Count": count,
                    "WithFaceImage": 0,
                    "WithBodyImage": 0,
                    "WithBackgroud": 0,
                    "SimpleInfo": 0,
                    "WithFeature": 0,
                    "NeedTime": 1,
                },
            )

    def list_added_faces(self) -> list[dict]:
        """The camera's own enrolled Allow List (distinct from SnapedFaces,
        which is the historical detection log) — [{"Id", "GrpId", "Name"}, ...].
        Requires Search immediately before GetByIndex in the same session;
        the device holds server-side query state from Search that GetByIndex
        reads, and returns an empty list without it."""
        with self._lock:
            search_result = self._post("/API/AI/AddedFaces/Search", {"MsgId": "", "FaceInfo": [{"GrpId": ALLOW_LIST_GROUP_ID}]})
            total = search_result["data"]["Count"]
            if total == 0:
                # Distinguishing this from a network failure matters: under an
                # unstable connection, a mid-sequence reconnect can land this
                # Search call on a freshly re-logged-in session, which has been
                # observed to genuinely succeed (200 OK) while reporting Count=0
                # even though the Allow List is not actually empty — not
                # something this client can detect or retry around by itself,
                # since the HTTP call itself raised no error.
                self.stats.request_empty += 1
                logger.warning(
                    "Honeywell: REQUEST -> EMPTY path=/API/AI/AddedFaces/Search host=%s — either the Allow "
                    "List is genuinely empty, or (if that seems wrong) a session reconnect mid-sequence "
                    "caused a false empty result; retry the sync if the Allow List is known to have entries.",
                    self._base,
                )
                return []
            result = self._post(
                "/API/AI/AddedFaces/GetByIndex",
                {"MsgId": "", "StartIndex": 0, "Count": total, "SimpleInfo": 1, "WithImage": 0, "WithFeature": 0},
            )
            return result["data"].get("FaceInfo", [])

    def get_added_face_photo(self, face_id: int) -> bytes | None:
        with self._lock:
            result = self._post(
                "/API/AI/AddedFaces/GetById",
                {"MsgId": "", "FacesId": [face_id], "SimpleInfo": 0, "WithImage": 1, "WithFeature": 0},
            )
        faces = result["data"].get("FaceInfo", [])
        if not faces or not faces[0].get("Image1"):
            return None
        return base64.b64decode(faces[0]["Image1"])

    def get_added_face_with_photo(self, face_id: int) -> dict | None:
        """Same GetById call as get_added_face_photo (SimpleInfo=0 already
        returns the full record, not just the image) but returns the whole
        entry — including IdCode, the camera's own optional per-person code
        field shown as "Id Code" in its web UI — so a caller enrolling a new
        person can pick that up without a second round-trip against a
        device that's already fragile under repeated requests."""
        with self._lock:
            result = self._post(
                "/API/AI/AddedFaces/GetById",
                {"MsgId": "", "FacesId": [face_id], "SimpleInfo": 0, "WithImage": 1, "WithFeature": 0},
            )
        faces = result["data"].get("FaceInfo", [])
        if not faces or not faces[0].get("Image1"):
            return None
        entry = dict(faces[0])
        entry["_photo_bytes"] = base64.b64decode(entry.pop("Image1"))
        return entry

    def get_added_face_by_id(self, face_id: int) -> dict | None:
        """Single-entry Allow List lookup by the camera's own Id — lighter
        than list_added_faces() when only one person's current name is
        needed (e.g. RecognitionProvider.get_person's live fallback)."""
        with self._lock:
            result = self._post(
                "/API/AI/AddedFaces/GetById",
                {"MsgId": "", "FacesId": [face_id], "SimpleInfo": 1, "WithImage": 0, "WithFeature": 0},
            )
        faces = result["data"].get("FaceInfo", [])
        return faces[0] if faces else None

    def add_face(self, name: str, jpeg_bytes: bytes) -> dict:
        image_b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        with self._lock:
            return self._post(
                "/API/AI/Faces/Add",
                {
                    "MsgId": 0,
                    "Count": 1,
                    "FaceInfo": [
                        {
                            "GrpId": ALLOW_LIST_GROUP_ID,
                            "Name": name,
                            "Time": 0,
                            "Similarity": 0,
                            "Sex": 0,
                            "Age": 0,
                            "Chn": 0,
                            "ModifyCnt": 0,
                            "Image1": image_b64,
                        }
                    ],
                },
            )


_clients: dict[str, CameraClient] = {}


def get_camera_client(host: str, user: str, password: str, port: int = config.CAMERA_ADMIN_PORT) -> CameraClient:
    """Reuses one CameraClient (and its logged-in session) per physical
    device, so we don't re-login on every enrollment."""
    key = f"{host}:{port}"
    if key not in _clients:
        _clients[key] = CameraClient(host, user, password, port)
    return _clients[key]


def get_all_client_stats() -> dict[str, dict]:
    """host:port -> _Stats snapshot, for main.py's /api/stats — lets the
    dashboard tell CAMERA/API/NETWORK trouble apart from "it's just quiet"
    without reading logs."""
    return {key: client.stats.as_dict() for key, client in _clients.items()}


def sync_face_to_all_devices(name: str, jpeg_bytes: bytes, exclude_host: str | None = None) -> dict:
    """Pushes an enrolled person to every active physical device's onboard
    Allow List (one push per device, not per camera channel — several
    camera rows can share one physical unit). exclude_host skips a device —
    used when the person was JUST fetched from that same device's own Allow
    List (main.py's primary-camera sync), so it doesn't get a redundant
    second entry pushed back to where it already came from."""
    results = {}
    for device in camera_db.list_active_devices():
        host = device["host"]
        if host == exclude_host:
            continue
        if not device.get("user") or not device.get("password"):
            results[host] = {"synced": False, "error": "no admin credentials configured"}
            continue
        client = get_camera_client(host, device["user"], device["password"], device.get("admin_port", 443))
        try:
            client.add_face(name, jpeg_bytes)
            results[host] = {"synced": True}
            logger.info("Synced %s to Allow List on %s", name, host)
        except Exception as e:
            results[host] = {"synced": False, "error": str(e)}
            logger.error("Failed to sync %s to device %s: %s", name, host, e)
    return results


camera_client = get_camera_client(config.CAMERA_HOST, config.CAMERA_USER, config.CAMERA_PASSWORD)
