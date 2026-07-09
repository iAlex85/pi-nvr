from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_admin, get_current_user
from app.cameras import onvif_discovery
from app.cameras.crypto import decrypt, encrypt
from app.database import get_db
from app.models import Camera, CameraProtocol, RecordingMode, User

logger = logging.getLogger("pi_nvr.cameras")
router = APIRouter()


class CameraIn(BaseModel):
    name: str
    group: str | None = None
    protocol: CameraProtocol = CameraProtocol.rtsp
    rtsp_url: str
    rtsp_substream_url: str | None = None
    username: str | None = None
    password: str | None = None
    onvif_host: str | None = None
    onvif_port: int | None = None
    onvif_username: str | None = None
    onvif_password: str | None = None
    storage_target_id: int | None = None
    enabled: bool = True
    recording_mode: RecordingMode = RecordingMode.off
    motion_enabled: bool = False
    rotate_degrees: int = 0
    mirror: bool = False


class CameraOut(BaseModel):
    id: int
    name: str
    group: str | None
    protocol: CameraProtocol
    rtsp_url: str
    enabled: bool
    recording_mode: RecordingMode
    motion_enabled: bool
    supports_ptz: bool
    storage_target_id: int | None

    class Config:
        from_attributes = True


def _camera_to_out(camera: Camera) -> CameraOut:
    return CameraOut.model_validate(camera)


@router.get("", response_model=list[CameraOut])
def list_cameras(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_camera_to_out(c) for c in db.query(Camera).all()]


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return _camera_to_out(camera)


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
async def create_camera(
    payload: CameraIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    camera = Camera(
        name=payload.name,
        group=payload.group,
        protocol=payload.protocol,
        rtsp_url=payload.rtsp_url,
        rtsp_substream_url=payload.rtsp_substream_url,
        username=payload.username,
        password_enc=encrypt(payload.password),
        onvif_host=payload.onvif_host,
        onvif_port=payload.onvif_port,
        onvif_username=payload.onvif_username,
        onvif_password_enc=encrypt(payload.onvif_password),
        storage_target_id=payload.storage_target_id,
        enabled=payload.enabled,
        recording_mode=payload.recording_mode,
        motion_enabled=payload.motion_enabled,
        rotate_degrees=payload.rotate_degrees,
        mirror=payload.mirror,
    )
    db.add(camera)
    db.flush()

    if camera.enabled:
        await request.app.state.camera_manager.watch_camera(camera.id)

    logger.info("Camera '%s' created (id=%s)", camera.name, camera.id)
    return _camera_to_out(camera)


@router.put("/{camera_id}", response_model=CameraOut)
async def update_camera(
    camera_id: int,
    payload: CameraIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    was_enabled = camera.enabled

    camera.name = payload.name
    camera.group = payload.group
    camera.protocol = payload.protocol
    camera.rtsp_url = payload.rtsp_url
    camera.rtsp_substream_url = payload.rtsp_substream_url
    camera.username = payload.username
    if payload.password:
        camera.password_enc = encrypt(payload.password)
    camera.onvif_host = payload.onvif_host
    camera.onvif_port = payload.onvif_port
    camera.onvif_username = payload.onvif_username
    if payload.onvif_password:
        camera.onvif_password_enc = encrypt(payload.onvif_password)
    camera.storage_target_id = payload.storage_target_id
    camera.enabled = payload.enabled
    camera.recording_mode = payload.recording_mode
    camera.motion_enabled = payload.motion_enabled
    camera.rotate_degrees = payload.rotate_degrees
    camera.mirror = payload.mirror
    db.add(camera)
    db.flush()

    cam_mgr = request.app.state.camera_manager
    if was_enabled and not camera.enabled:
        await cam_mgr.unwatch_camera(camera.id)
    elif camera.enabled:
        await cam_mgr.watch_camera(camera.id)

    return _camera_to_out(camera)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(
    camera_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    await request.app.state.camera_manager.unwatch_camera(camera_id)
    db.delete(camera)


@router.get("/{camera_id}/status")
def camera_status(camera_id: int, request: Request, user: User = Depends(get_current_user)):
    status_obj = request.app.state.camera_manager.get_status(camera_id)
    if status_obj is None:
        return {"camera_id": camera_id, "online": False, "last_seen": None}
    return dataclasses_asdict_safe(status_obj)


@router.get("/status/all")
def all_camera_status(request: Request, user: User = Depends(get_current_user)):
    return {
        cam_id: dataclasses_asdict_safe(s)
        for cam_id, s in request.app.state.camera_manager.all_status().items()
    }


@router.post("/discover")
async def discover_cameras(user: User = Depends(require_admin)):
    devices = await onvif_discovery.discover()
    return {"devices": [d.to_dict() for d in devices]}


def dataclasses_asdict_safe(obj) -> dict:
    import dataclasses

    return dataclasses.asdict(obj)
