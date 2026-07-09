from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import Camera, Recording, User
from app.recording import scheduler as schedule_mod

router = APIRouter()


class RecordingOut(BaseModel):
    id: int
    camera_id: int
    file_path: str
    started_at: str
    ended_at: str | None
    trigger: str
    size_bytes: int | None
    duration_seconds: float | None
    locked: bool

    class Config:
        from_attributes = True


class ScheduleWindow(BaseModel):
    day: str
    start: str
    end: str


@router.post("/{camera_id}/start")
async def start_recording(camera_id: int, request: Request, user: User = Depends(require_admin)):
    await request.app.state.recording_engine.start_manual(camera_id)
    return {"ok": True, "recording": True}


@router.post("/{camera_id}/stop")
async def stop_recording(camera_id: int, request: Request, user: User = Depends(require_admin)):
    await request.app.state.recording_engine.stop_manual(camera_id)
    return {"ok": True, "recording": False}


@router.get("/{camera_id}/active")
def recording_active(camera_id: int, request: Request, user: User = Depends(get_current_user)):
    return {"camera_id": camera_id, "recording": request.app.state.recording_engine.is_recording(camera_id)}


@router.get("", response_model=list[RecordingOut])
def list_recordings(
    camera_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Recording)
    if camera_id is not None:
        query = query.filter(Recording.camera_id == camera_id)
    return [
        RecordingOut(
            id=r.id,
            camera_id=r.camera_id,
            file_path=r.file_path,
            started_at=r.started_at.isoformat() if r.started_at else None,
            ended_at=r.ended_at.isoformat() if r.ended_at else None,
            trigger=r.trigger,
            size_bytes=r.size_bytes,
            duration_seconds=r.duration_seconds,
            locked=r.locked,
        )
        for r in query.order_by(Recording.started_at.desc()).limit(500).all()
    ]


@router.post("/{recording_id}/lock")
def lock_recording(recording_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    rec = db.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    rec.locked = True
    db.add(rec)
    return {"ok": True}


@router.post("/{recording_id}/unlock")
def unlock_recording(recording_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    rec = db.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    rec.locked = False
    db.add(rec)
    return {"ok": True}


@router.get("/{camera_id}/schedule", response_model=list[ScheduleWindow])
def get_schedule(camera_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if db.get(Camera, camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return schedule_mod.get_schedule(camera_id)


@router.put("/{camera_id}/schedule")
def set_schedule(
    camera_id: int,
    windows: list[ScheduleWindow],
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    if db.get(Camera, camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    try:
        schedule_mod.set_schedule(camera_id, [w.model_dump() for w in windows])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
