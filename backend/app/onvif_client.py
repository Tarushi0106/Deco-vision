"""Fetch-on-first-play clip caching from a camera's own onboard recording
(ONVIF Profile G / Replay) - a sighting is logged as just a timestamp+
duration (no video), and the first time someone actually clicks play, we
pull that window from the camera and cache the result locally so every
later play of the same clip is instant. Bounds local storage to "clips
someone actually watched" instead of "every sighting, forever" (the old
self-recording behavior), without paying the camera-fetch latency on every
single play.

Investigated and validated live against camera 1 (Entry/Exit, host
103.204.0.126) this session:
  - The device's ONVIF services (GetCapabilities/GetServices) advertise
    Recording, SearchRecording, and Replay at /onvif/{Recording,
    SearchRecording,Replay} on port 80, authenticated with the same admin
    HTTP Digest credentials already used for the AI API (camera_client.py).
    Camera 2/3's device (103.204.0.122) advertises the same three services
    over HTTPS on its admin port (8443) - also confirmed live - but reports
    its own internal LAN IP (173.16.9.3) in XAddr, which is unreachable
    from here; always build URLs from the camera's own configured
    host/port (camera_db), never from a device's self-reported XAddr.
  - GetReplayUri does NOT actually scope playback to a requested time
    window - it just echoes the device's overall recorded range. The real,
    working mechanism is a Hikvision-style playback RTSP URL:
    rtsp://host:port/rtsp/playback?channel=N&starttime=...&endtime=... -
    confirmed by hand-building one and pulling real HEVC frames for an
    exact requested window with ffmpeg.
  - Given that, there's no real need to round-trip FindRecordings/
    GetRecordingSearchResults/GetReplayUri on every playback request (slow,
    and one malformed request during this same investigation briefly hung
    the device's ONVIF service) - we just build the URL directly.
  - Fetch latency is dominated by a large FIXED per-request cost, not clip
    length: a 5.8s window took ~121s, a 98s window took ~185s. Modeling
    that as roughly "~120s fixed + ~1x duration" (see the timeout in
    main.py's get_clip_video) - this is why caching the result after the
    first fetch matters far more than optimizing the fetch itself.

config.CAMERA_ONVIF_REPLAY_CHANNEL is the single source of truth for which
cameras use this path vs. old-style local self-recording.
"""

from __future__ import annotations

import datetime
import logging
import subprocess
from pathlib import Path

import imageio_ffmpeg

logger = logging.getLogger("dashboard.onvif")

REPLAY_PADDING_SECONDS = 2.0  # margin on both ends so we don't clip the start/end of a fast sighting
TRANSCODE_THREADS = 2


def build_replay_rtsp_url(host: str, port: int, user: str, password: str, channel: int,
                           start_ts: float, end_ts: float) -> str:
    start = datetime.datetime.fromtimestamp(start_ts, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = datetime.datetime.fromtimestamp(end_ts, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"rtsp://{user}:{password}@{host}:{port}/rtsp/playback"
        f"?channel={channel}&starttime={start}&endtime={end}&localtime=false"
    )


def fetch_replay_clip(replay_url: str, out_path: Path, timeout: float) -> Path | None:
    """Pulls the camera's own recorded footage for the requested window into
    out_path (the caller decides where - main.py caches it as this clip's
    permanent local file, same directory old self-recorded clips used), and
    transcodes it to browser-playable H.264 MP4 (the camera records HEVC,
    which most browsers won't play in a <video> tag - same reason the old
    self-recorded clips were always transcoded before saving). Downscaling
    (old self-recorded clips capped at 960px too) and dropping audio (the
    old clips never had any either - cv2.VideoWriter can't capture it) keep
    the transcode itself cheap so it doesn't add meaningfully on top of the
    fetch's own (large, mostly-fixed - see module docstring) latency.

    Returns out_path on success, None on failure (out_path is cleaned up in
    that case, never left as a partial/broken file)."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    try:
        result = subprocess.run(
            [
                ffmpeg_exe, "-y", "-rtsp_transport", "tcp", "-i", replay_url,
                "-vf", "scale='min(960,iw)':'-2'",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an",
                "-threads", str(TRANSCODE_THREADS),
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path),
            ],
            capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.error("Camera replay fetch timed out for %s", replay_url.split("@")[-1])
        out_path.unlink(missing_ok=True)
        return None
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        logger.error(
            "Camera replay fetch failed: %s",
            result.stderr.decode(errors="replace")[-1500:],
        )
        out_path.unlink(missing_ok=True)
        return None
    return out_path
