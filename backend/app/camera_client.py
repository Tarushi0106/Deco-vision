"""Talks to the Honeywell camera's own admin REST API (the same one its
web UI uses) to push enrolled people into its onboard face database
(Allow List), so the camera's own recognition picks them up immediately
— not just our dashboard's local model.
"""

import base64
import logging

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

    def _login(self) -> None:
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
        search_result = self._post("/API/AI/AddedFaces/Search", {"MsgId": "", "FaceInfo": [{"GrpId": ALLOW_LIST_GROUP_ID}]})
        total = search_result["data"]["Count"]
        if total == 0:
            return []
        result = self._post(
            "/API/AI/AddedFaces/GetByIndex",
            {"MsgId": "", "StartIndex": 0, "Count": total, "SimpleInfo": 1, "WithImage": 0, "WithFeature": 0},
        )
        return result["data"].get("FaceInfo", [])

    def get_added_face_photo(self, face_id: int) -> bytes | None:
        result = self._post(
            "/API/AI/AddedFaces/GetById",
            {"MsgId": "", "FacesId": [face_id], "SimpleInfo": 0, "WithImage": 1, "WithFeature": 0},
        )
        faces = result["data"].get("FaceInfo", [])
        if not faces or not faces[0].get("Image1"):
            return None
        return base64.b64decode(faces[0]["Image1"])

    def add_face(self, name: str, jpeg_bytes: bytes) -> dict:
        image_b64 = base64.b64encode(jpeg_bytes).decode("ascii")
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


def sync_face_to_all_devices(name: str, jpeg_bytes: bytes) -> dict:
    """Pushes an enrolled person to every active physical device's onboard
    Allow List (one push per device, not per camera channel — several
    camera rows can share one physical unit)."""
    results = {}
    for device in camera_db.list_active_devices():
        host = device["host"]
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
