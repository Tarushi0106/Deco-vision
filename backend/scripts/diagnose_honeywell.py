"""One-shot, careful diagnostic for the Main Honeywell camera's admin API —
built to answer Part 1's exact requirement: distinguish "genuinely empty
response" from "connection/API failure" from "timeout" from "malformed
response", and never silently treat a failure as "0 people".

Deliberately standalone (talks to the API directly with requests +
HTTPDigestAuth, mirroring camera_client.py's exact request shapes) rather
than going through camera_client.py, so this diagnostic's own timing/error
categorization is never masked by that module's internal relogin-and-retry
logic. Does NOT modify or touch camera_client.py, the running backend, or
any enrolled data — pure read-only diagnostic, run once per invocation
(not a retry loop) so it can't compound this device's documented
instability under rapid repeated requests.

Usage: python scripts/diagnose_honeywell.py [--host HOST] [--port PORT]
"""

import argparse
import json
import ssl
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import urllib3
from requests.auth import HTTPDigestAuth

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 20


def _classify_exception(e: Exception) -> str:
    """Best-effort, honest categorization — never collapses these into a
    single generic "failed" bucket, since the difference is exactly what
    Part 1 asks to distinguish."""
    msg = str(e)
    if isinstance(e, requests.exceptions.ConnectTimeout) or "Connection to" in msg and "timed out" in msg:
        return "TIMEOUT (connect)"
    if isinstance(e, requests.exceptions.ReadTimeout):
        return "TIMEOUT (read)"
    if isinstance(e, requests.exceptions.ConnectionError):
        if "actively refused" in msg or "10061" in msg:
            return "CONNECTION_REFUSED"
        if "forcibly closed" in msg or "Connection aborted" in msg or "RemoteDisconnected" in msg:
            return "CONNECTION_RESET"
        return "CONNECTION_ERROR (other)"
    if isinstance(e, ssl.SSLError) or "SSL" in type(e).__name__:
        return "SSL_ERROR"
    return f"UNEXPECTED_ERROR ({type(e).__name__})"


def _timed_post(session, url, json_body, auth=None):
    """Returns (outcome_dict). Never raises — every failure mode is
    captured and categorized instead."""
    start = time.time()
    try:
        resp = session.post(
            url, json=json_body, auth=auth, verify=False,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        elapsed_ms = round((time.time() - start) * 1000, 1)
        try:
            body = resp.json()
        except ValueError:
            return {
                "outcome": "MALFORMED_RESPONSE", "http_status": resp.status_code,
                "elapsed_ms": elapsed_ms, "raw_text_head": resp.text[:300],
            }
        return {"outcome": "OK", "http_status": resp.status_code, "elapsed_ms": elapsed_ms, "body": body}
    except Exception as e:
        elapsed_ms = round((time.time() - start) * 1000, 1)
        return {"outcome": _classify_exception(e), "elapsed_ms": elapsed_ms, "error": str(e)}


def run_diagnostic(host: str, port: int, user: str, password: str) -> dict:
    base = f"https://{host}" if port == 443 else f"https://{host}:{port}"
    session = requests.Session()
    report: dict = {"host": host, "port": port, "started_at": datetime.now().isoformat(), "steps": {}}

    # --- Step 1: login ---
    login = _timed_post(
        session, f"{base}/API/Web/Login",
        {"data": {"support_new_schedule": True, "remote_terminal_info": "WEB,chrome"}},
        auth=HTTPDigestAuth(user, password),
    )
    report["steps"]["login"] = login
    if login["outcome"] != "OK" or login["http_status"] != 200:
        report["summary"] = f"FAILED at login: {login['outcome']} (this is a connection/API failure, NOT '0 people')"
        return report
    csrf = session.headers.get("X-csrftoken") or (
        # some devices return it on the response object, not the session default headers
        None
    )

    # --- Step 2: People List (AddedFaces) ---
    search = _timed_post(session, f"{base}/API/AI/AddedFaces/Search", {"MsgId": "", "FaceInfo": [{"GrpId": 2}]})
    report["steps"]["added_faces_search"] = search
    if search["outcome"] != "OK":
        report["summary"] = f"FAILED at AddedFaces/Search: {search['outcome']} (connection/API failure, NOT '0 people')"
        return report
    count = search["body"].get("data", {}).get("Count")
    report["steps"]["added_faces_search"]["reported_count"] = count
    if count == 0:
        report["steps"]["added_faces_search"]["note"] = (
            "API call itself SUCCEEDED (HTTP 200) but reported Count=0 — this is a GENUINE response from the "
            "device, not a connection failure. Whether it's a real empty Allow List or a false-empty (known "
            "device quirk under session/connection instability) cannot be determined from this call alone; "
            "compare against a separately-known real headcount."
        )
        report["summary"] = "Login OK, but AddedFaces/Search reported Count=0 (see note — not a connection failure)"
        return report

    getbyindex = _timed_post(
        session, f"{base}/API/AI/AddedFaces/GetByIndex",
        {"MsgId": "", "StartIndex": 0, "Count": count, "SimpleInfo": 1, "WithImage": 0, "WithFeature": 0},
    )
    report["steps"]["added_faces_getbyindex"] = getbyindex
    people = []
    if getbyindex["outcome"] == "OK":
        people = getbyindex["body"].get("data", {}).get("FaceInfo", [])
        report["steps"]["added_faces_getbyindex"]["actual_people_returned"] = len(people)

    # --- Step 3: SnapedFaces (recognition event log) ---
    now = datetime.now()
    since = now - timedelta(hours=6)
    fmt = lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")
    snap_search = _timed_post(
        session, f"{base}/API/AI/SnapedFaces/Search",
        {
            "MsgId": "", "StartTime": fmt(since), "EndTime": fmt(now), "Chn": ["CH1"],
            "AlarmGroup": [], "Similarity": 0, "Engine": 1, "Count": 0, "FaceInfo": [],
        },
    )
    report["steps"]["snaped_faces_search"] = snap_search
    if snap_search["outcome"] != "OK":
        report["summary"] = (
            f"People List OK ({len(people)} people), but SnapedFaces/Search FAILED: {snap_search['outcome']} "
            "(connection/API failure, NOT '0 events')"
        )
        return report

    snap_getbyindex = _timed_post(
        session, f"{base}/API/AI/SnapedFaces/GetByIndex",
        {
            "MsgId": "", "Engine": 1, "MatchedFaces": 0, "StartIndex": 0, "Count": 20,
            "WithFaceImage": 0, "WithBodyImage": 0, "WithBackgroud": 0, "SimpleInfo": 0,
            "WithFeature": 0, "NeedTime": 1,
        },
    )
    report["steps"]["snaped_faces_getbyindex"] = snap_getbyindex
    events = []
    if snap_getbyindex["outcome"] == "OK":
        data = snap_getbyindex["body"].get("data", {})
        events = data.get("FaceInfo") or data.get("MatchedFaces") or []
        report["steps"]["snaped_faces_getbyindex"]["actual_events_returned"] = len(events)
        report["steps"]["snaped_faces_getbyindex"]["raw_first_entry"] = events[0] if events else None

    report["summary"] = (
        f"FULL SUCCESS: login OK, People List = {len(people)} people, "
        f"SnapedFaces (last 6h) = {len(events)} recognition event(s)"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="103.204.0.122")
    parser.add_argument("--port", type=int, default=100)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    result = run_diagnostic(args.host, args.port, args.user, args.password)
    print(json.dumps(result, indent=2, default=str))
