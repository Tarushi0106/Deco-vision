"""Camera ingestion abstraction.

Every capability (face recognition, object detection, color analysis,
footfall counting) is written against `VideoSource`, never against a
concrete source. Today that's a webcam; once the Honeywell SESDK binaries
and a real camera are available, `HoneywellSource` drops in without
touching anything downstream.
"""

from abc import ABC, abstractmethod
from urllib.parse import quote

import cv2
import numpy as np


class VideoSource(ABC):
    @abstractmethod
    def get_frame(self) -> np.ndarray | None:
        """Return the latest BGR frame, or None if unavailable."""

    @abstractmethod
    def release(self) -> None:
        ...


class WebcamSource(VideoSource):
    def __init__(self, index: int = 0):
        self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open webcam at index {index}")

    def get_frame(self) -> np.ndarray | None:
        ok, frame = self._cap.read()
        return frame if ok else None

    def release(self) -> None:
        self._cap.release()


class FileSource(VideoSource):
    """Loops a video file — useful for testing without a live webcam."""

    def __init__(self, path: str):
        self._path = path
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open video file {path}")

    def get_frame(self) -> np.ndarray | None:
        ok, frame = self._cap.read()
        if not ok:
            self._cap.release()
            self._cap = cv2.VideoCapture(self._path)
            ok, frame = self._cap.read()
        return frame if ok else None

    def release(self) -> None:
        self._cap.release()


class RtspSource(VideoSource):
    """Pulls the live stream directly over standard RTSP — the Honeywell
    camera exposes this alongside its proprietary SDK/web protocol, so no
    SESDKWrapper binary is needed for raw video access.
    """

    def __init__(self, host: str, port: int, user: str, password: str, stream_path: str):
        auth = f"{quote(user, safe='')}:{quote(password, safe='')}"
        url = f"rtsp://{auth}@{host}:{port}{stream_path}"
        self._cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        # minimize internal buffering so reads return the newest frame, not a
        # backlog — critical once capture can momentarily fall behind (e.g.
        # while detection is running on a shared thread)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open RTSP stream at {host}:{port}{stream_path}")

    def get_frame(self) -> np.ndarray | None:
        ok, frame = self._cap.read()
        return frame if ok else None

    def release(self) -> None:
        self._cap.release()


# --- Placeholder for later (only needed for control features RTSP can't
# reach: PTZ, onboard face-database queries, alarms/events, recording
# search/download) ---
#
# class HoneywellSource(VideoSource):
#     """Wraps SESDKWrapper.dll via ctypes: se_sdk_wrapper_init ->
#     se_create_device -> se_device_login_ex -> se_start_preview with a
#     video_render_callback that copies SE_VIDEO_DATA planes into a
#     numpy array. Not implemented until binaries are available — video
#     itself no longer needs this now that plain RTSP access works.
#     """
