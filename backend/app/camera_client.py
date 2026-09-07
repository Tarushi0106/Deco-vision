"""Talks to the Honeywell camera's own admin REST API (the same one its
web UI uses) to push enrolled people into its onboard face database
(Allow List), so the camera's own recognition picks them up immediately
— not just our dashboard's local model.
"""

import base64
import logging
import threading

import requests
import urllib3
from requests.auth import HTTPDigestAuth

from . import camera_db, config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("dashboard.camera_client")

ALLOW_LIST_GROUP_ID = 2


class CameraClient:
    def __init__(self, host: str, user: str, password: str, port: int = config.CAMERA_ADMIN_PORT):
        self._base = f"https://{host}" if port == 443 else f"https://{host}:{port}"
        self._user = user
        self._password = password
        self._session = requests.Session()
        self._session.verify = False
        self._logged_in = False
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

    def _login(self) -> None:
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
        resp = self._session.post(
            f"{self._base}/API/Web/Login",
            auth=HTTPDigestAuth(self._user, self._password),
            json={"data": {"support_new_schedule": True, "remote_terminal_info": "WEB,chrome"}},
            verify=False,
            timeout=15,
        )
        resp.raise_for_status()
        # required on every subsequent request, or the server resets the connection
        csrf_token = resp.headers.get("X-csrftoken")
        if csrf_token:
            self._session.headers.update({"X-csrftoken": csrf_token})
        self._logged_in = True
        logger.info("Logged into camera admin API (%s)", self._base)

    def _post(self, path: str, data: dict) -> dict:
        # Callers hold self._lock for their whole request sequence (see
        # list_added_faces/search_snaped_faces/etc.) — not acquired here,
        # since a sequence like Search-then-GetByIndex must stay uninterrupted
        # across both calls, not just within each individual POST.
        if not self._logged_in:
            self._login()
        try:
            resp = self._session.post(
                f"{self._base}{path}",
                json={"version": "1.0", "data": data},
                verify=False,
                timeout=20,
            )
            if resp.status_code == 401:
                raise requests.exceptions.ConnectionError("401")
        except requests.exceptions.RequestException:
            # session/CSRF token expired or connection was reset by the device — relogin once
            self._logged_in = False
            self._login()
            resp = self._session.post(
                f"{self._base}{path}", json={"version": "1.0", "data": data}, verify=False, timeout=20
            )
        resp.raise_for_status()
        return resp.json()

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
                logger.warning(
                    "AddedFaces/Search on %s reported Count=0 — either the Allow List is genuinely empty, "
                    "or (if that seems wrong) a session reconnect mid-sequence caused a false empty result; "
                    "retry the sync if the Allow List is known to have entries.",
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
