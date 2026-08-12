"""
Shared snapshot-capture logic used by both the manual "Snap" button
(app/playback/routes.py) and CameraManager's periodic snapshot capture
(app/cameras/manager.py).

Takes plain values (rtsp_url, snapshot_dir, camera_id) rather than an
ORM Camera object deliberately: SQLAlchemy's default session config
expires an object's attributes after commit, so any caller building an
rtsp_url or a Path from a Camera object needs to do that *inside* an
open `session_scope()` block and hand this module the resulting plain
values -- not the object itself, which would raise a DetachedInstance
error the moment this module's slow ffmpeg I/O (several seconds) tries
to touch it after the caller's session has closed.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from pathlib import Path

from app.cameras.device_lock import acquire_device_slot, release_device_slot

logger = logging.getLogger("pi_nvr.cameras.snapshot")

CAPTURE_TIMEOUT_SECONDS = 15
# How long to wait for this device's single RTSP slot (see
# app/cameras/device_lock.py) before giving up -- shorter than live
# view's wait since a snapshot is a background/best-effort action, not
# something a user is actively staring at waiting for.
DEVICE_SLOT_WAIT_SECONDS = 3.0
# How many snapshot files to keep per camera. Manual "Snap" clicks were
# rare enough that this never mattered before, but periodic capture can
# now run every minute or so indefinitely -- without pruning, this
# directory grows forever.
KEEP_LATEST_N = 5


class SnapshotError(RuntimeError):
    pass


async def capture_snapshot(rtsp_url: str, snapshot_dir: Path, camera_id: int) -> Path:
    """Grabs a single frame from the camera's RTSP stream and writes it
    to disk, pruning older snapshots for this camera down to
    KEEP_LATEST_N. Raises SnapshotError on failure.

    Waits briefly for this camera's physical device's single RTSP slot
    (see app/cameras/device_lock.py) -- this project's target hardware
    doesn't tolerate a second RTSP connection attempt gracefully, so
    recording, live view, audio, and snapshot capture all share that
    one lock rather than each assuming they have the connection to
    themselves."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = snapshot_dir / f"cam{camera_id}_{ts}.jpg"

    got_slot = await acquire_device_slot(rtsp_url, timeout=DEVICE_SLOT_WAIT_SECONDS)
    if not got_slot:
        raise SnapshotError(
            "Camera busy: device already has an active connection "
            "(recording or live view)"
        )

    try:
        cmd = [
            "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-i", rtsp_url,
            "-frames:v", "1", str(out_path),
        ]
        proc = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=CAPTURE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            raise SnapshotError("Snapshot capture timed out")

        if proc.returncode != 0 or not out_path.exists():
            raise SnapshotError(f"ffmpeg failed: {stderr.decode(errors='replace')[:300]}")
    finally:
        release_device_slot(rtsp_url)

    _prune_old_snapshots(snapshot_dir, camera_id)
    return out_path


def _prune_old_snapshots(snapshot_dir: Path, camera_id: int) -> None:
    snapshots = sorted(snapshot_dir.glob(f"cam{camera_id}_*.jpg"))
    for stale in snapshots[:-KEEP_LATEST_N]:
        try:
            stale.unlink()
        except OSError:
            pass
