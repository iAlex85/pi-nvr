"""
Single shared RTSP connection slot per physical camera device.
 
This project's target hardware (a dual-lens Jooan unit) exposes two ONVIF
"cameras" in the DB -- Bullet (ch00_0) and Fixed Lens (ch01_0) -- that are
actually two channels on the SAME physical device, and the device only
tolerates one RTSP connection at a time, across BOTH channels combined.
 
Every other lock in this codebase (recording's per-camera state, the
live-view MJPEG/audio locks in app/cameras/routes.py) is keyed by
`camera_id`, so it only prevents a camera from colliding with *itself*.
It does nothing to stop camera 1's continuous recording from colliding
with camera 2's live view, or either camera's live view from colliding
with a periodic snapshot capture -- all of which resolve to the same
physical device and the same single connection slot. That gap is what
made Spotlight view intermittently blank: whichever consumer's ffmpeg
happened to already hold the device's one slot silently starved
everyone else.
 
Locks here are keyed by RTSP host:port (extracted from the connection
URL each consumer already builds), not by camera_id, so recording,
live MJPEG, live audio, and snapshot capture all serialize against each
other correctly regardless of which camera_id they were requested for.
"""
from __future__ import annotations
 
import asyncio
from urllib.parse import urlparse
 
_device_locks: dict[str, asyncio.Lock] = {}
 
 
def _device_key(rtsp_url: str) -> str:
    parsed = urlparse(rtsp_url)
    return f"{parsed.hostname}:{parsed.port or 554}"
 
 
def get_device_lock(rtsp_url: str) -> asyncio.Lock:
    key = _device_key(rtsp_url)
    lock = _device_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _device_locks[key] = lock
    return lock
 
 
async def acquire_device_slot(rtsp_url: str, timeout: float | None = None) -> bool:
    """Tries to acquire the physical device's single RTSP slot.
 
    Returns True if acquired -- caller MUST call release_device_slot()
    when done, in a `finally` block, exactly once. Returns False if
    `timeout` elapsed without acquiring (caller should NOT call
    release_device_slot() in that case). Pass timeout=None to wait
    indefinitely (used by recording, which should simply wait its turn
    rather than give up)."""
    lock = get_device_lock(rtsp_url)
    if timeout is None:
        await lock.acquire()
        return True
    try:
        await asyncio.wait_for(lock.acquire(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
 
 
def release_device_slot(rtsp_url: str) -> None:
    lock = get_device_lock(rtsp_url)
    if lock.locked():
        lock.release()
 
 
# --------------------------------------------------------------------------
# Priority preemption. Background consumers (motion detection, the
# continuous/ring-buffer recording pipeline) can tolerate a brief gap --
# a few seconds of not watching, or a slightly early segment cutover --
# far better than a person actively looking at Live view can tolerate
# queueing behind them. Rather than have live view just wait its turn
# in acquire_device_slot's FIFO, it signals "please let go" via
# request_release() and background consumers check release_requested()
# at their own natural checkpoints (a recording segment boundary, a
# motion-detector frame-read loop) and voluntarily terminate/release.
# --------------------------------------------------------------------------
 
_release_requests: dict[str, asyncio.Event] = {}
 
 
def _get_release_event(rtsp_url: str) -> asyncio.Event:
    key = _device_key(rtsp_url)
    ev = _release_requests.get(key)
    if ev is None:
        ev = asyncio.Event()
        _release_requests[key] = ev
    return ev
 
 
def request_release(rtsp_url: str) -> None:
    _get_release_event(rtsp_url).set()
 
 
def release_requested(rtsp_url: str) -> bool:
    return _get_release_event(rtsp_url).is_set()
 
 
def clear_release_request(rtsp_url: str) -> None:
    _get_release_event(rtsp_url).clear()
 
 
async def acquire_device_slot_priority(rtsp_url: str, timeout: float) -> bool:
    """Like acquire_device_slot, but first asks the current holder to
    yield -- see request_release() above. Used by live view/audio/
    snapshot, which should preempt background consumers rather than
    queue behind them."""
    request_release(rtsp_url)
    got = await acquire_device_slot(rtsp_url, timeout=timeout)
    if got:
        clear_release_request(rtsp_url)
    return got
