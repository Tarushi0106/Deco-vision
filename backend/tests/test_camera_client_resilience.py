"""Targeted tests for this session's Honeywell-robustness fixes:
- _classify_exception correctly distinguishes timeout/reset/refused/http/
  auth/malformed (never collapses these into "no people"/"no events").
- _post is bounded (never more than config.HONEYWELL_MAX_REQUEST_ATTEMPTS
  real HTTP calls for one logical request) — no request storm against a
  device that only reliably serves one request per connection.
- Stats counters (_Stats) move on the right event, so /api/stats can
  distinguish CAMERA/API/NETWORK trouble from "it's just quiet".

Never touches the network — requests.Session.post is monkeypatched per test.
"""

import requests

from app import camera_client, config
from app.camera_client import CameraClient, _classify_exception


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, json_raises=False, headers=None, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self._json_raises = json_raises
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err

    def json(self):
        if self._json_raises:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._json_body


def _client() -> CameraClient:
    return CameraClient("203.0.113.5", "admin", "secret", port=100)


# ---------------------------------------------------------------------------
# _classify_exception
# ---------------------------------------------------------------------------

def test_classify_connect_timeout():
    assert _classify_exception(requests.exceptions.ConnectTimeout("Connection to x timed out.")) == "TIMEOUT_CONNECT"


def test_classify_read_timeout():
    assert _classify_exception(requests.exceptions.ReadTimeout("read timed out")) == "TIMEOUT_READ"


def test_classify_connection_reset():
    e = requests.exceptions.ConnectionError("('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))")
    assert _classify_exception(e) == "CONNECTION_RESET"


def test_classify_connection_refused():
    e = requests.exceptions.ConnectionError("[WinError 10061] No connection could be made because the target machine actively refused it")
    assert _classify_exception(e) == "CONNECTION_REFUSED"


def test_classify_generic_connection_error():
    assert _classify_exception(requests.exceptions.ConnectionError("No route to host")) == "CONNECTION_ERROR"


def test_classify_auth_failed_vs_generic_http_error():
    resp_401 = _FakeResponse(status_code=401)
    err_401 = requests.exceptions.HTTPError("401")
    err_401.response = resp_401
    assert _classify_exception(err_401) == "AUTH_FAILED"

    resp_500 = _FakeResponse(status_code=500)
    err_500 = requests.exceptions.HTTPError("500")
    err_500.response = resp_500
    assert _classify_exception(err_500) == "HTTP_ERROR"


def test_classify_malformed_response():
    assert _classify_exception(ValueError("Expecting value")) == "MALFORMED_RESPONSE"


def test_classify_unexpected_falls_back_to_named_bucket():
    assert _classify_exception(RuntimeError("something else")) == "UNEXPECTED_ERROR(RuntimeError)"


# ---------------------------------------------------------------------------
# _login
# ---------------------------------------------------------------------------

def test_login_success_updates_stats(monkeypatch):
    client = _client()
    monkeypatch.setattr(client._session, "post", lambda *a, **k: _FakeResponse(200, headers={"X-csrftoken": "abc"}))
    assert client._login() is True
    assert client._logged_in is True
    assert client.stats.login_success == 1
    assert client.stats.login_fail == 0
    assert client.stats.last_login_at is not None


def test_login_failure_does_not_raise_and_counts(monkeypatch):
    client = _client()
    monkeypatch.setattr(client._session, "post", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectTimeout("timed out")))
    assert client._login() is False
    assert client._logged_in is False
    assert client.stats.login_fail == 1
    assert client.stats.login_success == 0


def test_reconnect_flag_credits_reconnect_counters_not_login(monkeypatch):
    client = _client()
    monkeypatch.setattr(client._session, "post", lambda *a, **k: _FakeResponse(200))
    client._login(is_reconnect=True)
    assert client.stats.reconnect_attempts == 1
    assert client.stats.reconnect_success == 1
    assert client.stats.login_success == 1  # still counted as an overall login success too


# ---------------------------------------------------------------------------
# _post — the one-request-per-connection reality
# ---------------------------------------------------------------------------

def test_post_success_on_first_try(monkeypatch):
    client = _client()
    client._logged_in = True  # skip the initial login for this test
    monkeypatch.setattr(client._session, "post", lambda *a, **k: _FakeResponse(200, json_body={"data": {"Count": 0}}))
    result = client._post("/API/AI/AddedFaces/Search", {})
    assert result == {"data": {"Count": 0}}
    assert client.stats.request_success == 1
    assert client.stats.last_successful_request_at is not None


def test_post_retries_once_after_reset_then_succeeds(monkeypatch):
    """The documented real-world shape: request #1 gets reset, a relogin
    succeeds, request #2 (the retry) succeeds."""
    client = _client()
    client._logged_in = True
    calls = {"post": 0, "login": 0}

    def fake_post(url, **kwargs):
        if "/API/Web/Login" in url:
            calls["login"] += 1
            return _FakeResponse(200)
        calls["post"] += 1
        if calls["post"] == 1:
            raise requests.exceptions.ConnectionError(
                "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
            )
        return _FakeResponse(200, json_body={"data": {"Count": 1}})

    monkeypatch.setattr(client._session, "post", fake_post)
    result = client._post("/API/AI/AddedFaces/Search", {})
    assert result == {"data": {"Count": 1}}
    assert calls["post"] == 2  # exactly one retry, not an unbounded loop
    assert calls["login"] == 1  # exactly one reconnect
    assert client.stats.request_reset == 1
    assert client.stats.request_success == 1
    assert client.stats.reconnect_success == 1


def test_post_never_exceeds_max_attempts_when_device_stays_down(monkeypatch):
    """This is the "no request storm" requirement: a persistently-resetting
    device must cap total HTTP attempts at config.HONEYWELL_MAX_REQUEST_ATTEMPTS,
    not retry indefinitely."""
    client = _client()
    client._logged_in = True
    calls = {"post": 0, "login": 0}

    def fake_post(url, **kwargs):
        if "/API/Web/Login" in url:
            calls["login"] += 1
            return _FakeResponse(200)  # relogin itself "succeeds" — device still resets the next real request
        calls["post"] += 1
        raise requests.exceptions.ConnectionError(
            "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
        )

    monkeypatch.setattr(client._session, "post", fake_post)
    try:
        client._post("/API/AI/SnapedFaces/Search", {})
        assert False, "expected the persistent failure to propagate"
    except requests.exceptions.ConnectionError:
        pass
    assert calls["post"] == config.HONEYWELL_MAX_REQUEST_ATTEMPTS
    assert calls["login"] == config.HONEYWELL_MAX_REQUEST_ATTEMPTS - 1
    assert client.stats.request_reset == config.HONEYWELL_MAX_REQUEST_ATTEMPTS


def test_post_stops_immediately_if_reconnect_itself_fails(monkeypatch):
    """No point spending a second doomed HTTP call if the relogin that was
    supposed to fix the session already failed."""
    client = _client()
    client._logged_in = True
    calls = {"post": 0, "login": 0}

    def fake_post(url, **kwargs):
        if "/API/Web/Login" in url:
            calls["login"] += 1
            raise requests.exceptions.ConnectTimeout("timed out")
        calls["post"] += 1
        raise requests.exceptions.ConnectionError("Remote end closed connection without response")

    monkeypatch.setattr(client._session, "post", fake_post)
    try:
        client._post("/API/AI/SnapedFaces/Search", {})
        assert False, "expected failure to propagate"
    except requests.exceptions.ConnectionError:
        pass
    assert calls["post"] == 1  # never attempted the retry once reconnect failed
    assert calls["login"] == 1
    assert client.stats.reconnect_fail == 1


def test_post_malformed_response_is_not_treated_as_empty(monkeypatch):
    """A malformed body gets the same bounded retry as any other failure
    (it's retried at most config.HONEYWELL_MAX_REQUEST_ATTEMPTS times, same
    cap, no special-casing) — the point under test is that it's classified
    and counted as MALFORMED_RESPONSE, never silently treated as an empty
    "0 people"/"0 events" result."""
    client = _client()
    client._logged_in = True
    monkeypatch.setattr(client._session, "post", lambda *a, **k: _FakeResponse(200, json_raises=True, text="<html>not json</html>"))
    try:
        client._post("/API/AI/AddedFaces/Search", {})
        assert False, "expected the malformed body to raise, not silently return empty data"
    except ValueError:
        pass
    assert client.stats.request_malformed == config.HONEYWELL_MAX_REQUEST_ATTEMPTS
    assert client.stats.request_success == 0
    assert client.stats.request_empty == 0


def test_list_added_faces_empty_result_is_counted_distinctly(monkeypatch):
    """A genuine HTTP-200 Count=0 must be counted as request_empty, never
    mixed into request_reset/timeout/etc — those are transport failures,
    this is a real (if ambiguous) API response."""
    client = _client()
    client._logged_in = True
    monkeypatch.setattr(client._session, "post", lambda *a, **k: _FakeResponse(200, json_body={"data": {"Count": 0}}))
    result = client.list_added_faces()
    assert result == []
    assert client.stats.request_empty == 1
    assert client.stats.request_reset == 0
    assert client.stats.request_timeout == 0
