"""
Playback API: browsing recordings by camera/date, seeking playback via
HTTP Range (delegated to Starlette's FileResponse, which already handles
Range headers so the browser <video> scrubber works without us
re-implementing byte-range serving), downloads, deletion, and snapshots.
"""
from __future__ import annotations

import asyncio
import collections
import datetime
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.cameras.url_utils import build_authenticated_rtsp_url
from app.database import get_db
from app.models import Camera, MotionEvent, Recording, User

logger = logging.getLogger("pi_nvr.playback")
router = APIRouter()


class CalendarDay(BaseModel):
    date: str
    recording_count: int
    motion_event_count: int


@router.get("/calendar", response_model=list[CalendarDay])
def calendar(
    camera_id: int,
    year: int,
    month: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    start = datetime.datetime(year, month, 1)
    end = (
        datetime.datetime(year + 1, 1, 1)
        if month == 12
        else datetime.datetime(year, month + 1, 1)
    )

    recordings = (
        db.query(Recording)
        .filter(Recording.camera_id == camera_id)
        .filter(Recording.started_at >= start, Recording.started_at < end)
        .all()
    )
    events = (
        db.query(MotionEvent)
        .filter(MotionEvent.camera_id == camera_id)
        .filter(MotionEvent.occurred_at >= start, MotionEvent.occurred_at < end)
        .all()
    )

    rec_counts: dict[str, int] = collections.Counter(
        r.started_at.date().isoformat() for r in recordings if r.started_at
    )
    event_counts: dict[str, int] = collections.Counter(
        e.occurred_at.date().isoformat() for e in events if e.occurred_at
    )

    all_days = set(rec_counts) | set(event_counts)
    return [
        CalendarDay(
            date=day,
            recording_count=rec_counts.get(day, 0),
            motion_event_count=event_counts.get(day, 0),
        )
        for day in sorted(all_days)
    ]


def _get_recording_or_404(db: Session, recording_id: int) -> Recording:
    rec = db.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    if not Path(rec.file_path).exists():
        raise HTTPException(status_code=410, detail="Recording file is missing on disk")
    return rec


VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
}


@router.get("/stream/{recording_id}")
def stream_recording(recording_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rec = _get_recording_or_404(db, recording_id)
    media_type = VIDEO_MIME_TYPES.get(Path(rec.file_path).suffix.lower(), "application/octet-stream")
    return FileResponse(rec.file_path, media_type=media_type)


@router.get("/download/{recording_id}")
def download_recording(recording_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rec = _get_recording_or_404(db, recording_id)
    filename = Path(rec.file_path).name
    return FileResponse(rec.file_path, media_type="application/octet-stream", filename=filename)


@router.delete("/{recording_id}")
def delete_recording(recording_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    rec = db.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    if rec.locked:
        raise HTTPException(status_code=409, detail="Recording is locked; unlock it before deleting")
    path = Path(rec.file_path)
    if path.exists():
        path.unlink()
    db.delete(rec)
    return {"ok": True}


@router.post("/snapshot/{camera_id}")
async def capture_snapshot(camera_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    storage = request.app.state.storage
    snapshot_dir = storage.snapshot_dir_for_camera(camera)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = snapshot_dir / f"cam{camera_id}_{ts}.jpg"

    rtsp_url = build_authenticated_rtsp_url(camera)

    cmd = [
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
        "-rtsp_transport", "tcp", "-i", rtsp_url,
        "-frames:v", "1", str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(status_code=504, detail="Snapshot capture timed out")

    if proc.returncode != 0 or not out_path.exists():
        logger.error("Snapshot failed for camera %s: %s", camera_id, stderr.decode(errors="replace"))
        raise HTTPException(status_code=502, detail="Snapshot capture failed")

    return {"ok": True, "path": str(out_path)}


@router.get("/snapshot/{camera_id}/latest")
def latest_snapshot(camera_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    storage = request.app.state.storage
    snapshot_dir = storage.snapshot_dir_for_camera(camera)
    if not snapshot_dir.exists():
        raise HTTPException(status_code=404, detail="No snapshots yet")
    snapshots = sorted(snapshot_dir.glob(f"cam{camera_id}_*.jpg"))
    if not snapshots:
        raise HTTPException(status_code=404, detail="No snapshots yet")
    return FileResponse(snapshots[-1], media_type="image/jpeg")
