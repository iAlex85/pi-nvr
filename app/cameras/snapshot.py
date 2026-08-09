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

logger = logging.getLogger("pi_nvr.cameras.snapshot")

CAPTURE_TIMEOUT_SECONDS = 15
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

    Callers are responsible for making sure this doesn't collide with
    another connection to the same camera (recording, live view, audio)
    -- this project's target hardware doesn't tolerate a second RTSP
    connection attempt gracefully."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = snapshot_dir / f"cam{camera_id}_{ts}.jpg"

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

    _prune_old_snapshots(snapshot_dir, camera_id)
    return out_path


def _prune_old_snapshots(snapshot_dir: Path, camera_id: int) -> None:
    snapshots = sorted(snapshot_dir.glob(f"cam{camera_id}_*.jpg"))
    for stale in snapshots[:-KEEP_LATEST_N]:
        try:
            stale.unlink()
        except OSError:
            pass
