from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import Camera, MotionEvent, MotionZone, User

router = APIRouter()


class MotionEventOut(BaseModel):
    id: int
    camera_id: int
    occurred_at: str
    score: float
    bounding_box: str | None
    snapshot_path: str | None

    class Config:
        from_attributes = True


class ZoneIn(BaseModel):
    zone_type: str  # "include" or "exclude"
    points: list[list[float]]  # normalized 0-1 [[x,y], ...]


class ZoneOut(BaseModel):
    id: int
    zone_type: str
    points: list[list[float]]


@router.get("/events", response_model=list[MotionEventOut])
def list_events(
    camera_id: int | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(MotionEvent)
    if camera_id is not None:
        query = query.filter(MotionEvent.camera_id == camera_id)
    events = query.order_by(MotionEvent.occurred_at.desc()).limit(min(limit, 1000)).all()
    return [
        MotionEventOut(
            id=e.id,
            camera_id=e.camera_id,
            occurred_at=e.occurred_at.isoformat(),
            score=e.score,
            bounding_box=e.bounding_box,
            snapshot_path=e.snapshot_path,
        )
        for e in events
    ]


@router.post("/{camera_id}/enable")
async def enable_motion(camera_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    camera.motion_enabled = True
    db.add(camera)
    db.flush()
    await request.app.state.motion_supervisor.refresh_camera(camera_id)
    return {"ok": True}


@router.post("/{camera_id}/disable")
async def disable_motion(camera_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    camera.motion_enabled = False
    db.add(camera)
    db.flush()
    await request.app.state.motion_supervisor.refresh_camera(camera_id)
    return {"ok": True}


@router.get("/{camera_id}/zones", response_model=list[ZoneOut])
def list_zones(camera_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    zones = db.query(MotionZone).filter(MotionZone.camera_id == camera_id).all()
    return [ZoneOut(id=z.id, zone_type=z.zone_type, points=json.loads(z.points_json)) for z in zones]


@router.post("/{camera_id}/zones", response_model=ZoneOut)
async def create_zone(
    camera_id: int,
    payload: ZoneIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    if payload.zone_type not in ("include", "exclude"):
        raise HTTPException(status_code=400, detail="zone_type must be 'include' or 'exclude'")
    zone = MotionZone(
        camera_id=camera_id,
        zone_type=payload.zone_type,
        points_json=json.dumps(payload.points),
    )
    db.add(zone)
    db.flush()
    await request.app.state.motion_supervisor.refresh_camera(camera_id)
    return ZoneOut(id=zone.id, zone_type=zone.zone_type, points=payload.points)


@router.delete("/zones/{zone_id}")
async def delete_zone(zone_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    zone = db.get(MotionZone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    camera_id = zone.camera_id
    db.delete(zone)
    db.flush()
    await request.app.state.motion_supervisor.refresh_camera(camera_id)
    return {"ok": True}
