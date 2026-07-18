"""
Shared helper for turning a Camera row's stored URL + credentials into an
authenticated RTSP URL ready to hand to ffmpeg/ffprobe.

This used to be duplicated (slightly differently each time, which is how
bugs like this creep in) across CameraManager's health-check probe,
RecordingEngine, the live-view MJPEG endpoint, and snapshot capture.
Consolidated here so every code path that talks to a camera authenticates
the same way.
"""
from __future__ import annotations

from app.cameras.crypto import decrypt
from app.models import Camera


def build_authenticated_rtsp_url(camera: Camera, substream: bool = False) -> str:
    """Returns the RTSP URL to actually connect with, injecting the
    camera's stored username/password if the URL doesn't already embed
    credentials and a username/password are on file.

    `substream=True` uses `rtsp_substream_url` if the camera has one
    configured, falling back to the main URL otherwise (used by motion
    detection and, for now, live view).
    """
    url = camera.rtsp_url
    if substream and camera.rtsp_substream_url:
        url = camera.rtsp_substream_url

    if "@" in url.split("://", 1)[-1]:
        # URL already has credentials embedded (e.g. user typed them
        # directly into the RTSP URL field) -- don't double them up.
        return url

    if not camera.username or not camera.password_enc:
        return url

    password = decrypt(camera.password_enc)
    if not password:
        return url

    scheme, rest = url.split("://", 1)
    return f"{scheme}://{camera.username}:{password}@{rest}"
