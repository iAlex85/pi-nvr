from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import StorageTarget, User
from app.storage import device_format

router = APIRouter()


class StorageTargetIn(BaseModel):
    name: str
    path: str
    kind: str = "local"
    is_default: bool = False


class StorageTargetOut(BaseModel):
    id: int
    name: str
    path: str
    kind: str
    is_default: bool

    class Config:
        from_attributes = True


class MountOut(BaseModel):
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    filesystem: str
    is_removable: bool
    used_percent: float


@router.get("/targets", response_model=list[StorageTargetOut])
def list_targets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(StorageTarget).all()


@router.post("/targets", response_model=StorageTargetOut)
def create_target(payload: StorageTargetIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if db.query(StorageTarget).filter(StorageTarget.name == payload.name).first():
        raise HTTPException(status_code=409, detail="A storage target with that name already exists")
    target = StorageTarget(**payload.model_dump())
    db.add(target)
    db.flush()
    return target


@router.delete("/targets/{target_id}")
def delete_target(target_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    target = db.get(StorageTarget, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Storage target not found")
    if target.cameras:
        raise HTTPException(status_code=409, detail="Reassign cameras off this target before deleting it")
    db.delete(target)
    return {"ok": True}


@router.get("/browse", response_model=list[MountOut])
def browse_mounts(request: Request, user: User = Depends(require_admin)):
    """Powers the storage-picker "Browse" button: lists local + removable
    (USB/SSD/HDD) + already-mounted network paths with usage stats."""
    mounts = request.app.state.storage.list_available_mounts()
    return [
        MountOut(
            path=m.path,
            total_bytes=m.total_bytes,
            used_bytes=m.used_bytes,
            free_bytes=m.free_bytes,
            filesystem=m.filesystem,
            is_removable=m.is_removable,
            used_percent=round((m.used_bytes / m.total_bytes) * 100, 1) if m.total_bytes else 0.0,
        )
        for m in mounts
    ]


@router.get("/targets/{target_id}/usage", response_model=MountOut)
def target_usage(target_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target = db.get(StorageTarget, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Storage target not found")
    info = request.app.state.storage.target_usage(target)
    if info is None:
        raise HTTPException(status_code=404, detail="Path not accessible")
    return MountOut(
        path=info.path,
        total_bytes=info.total_bytes,
        used_bytes=info.used_bytes,
        free_bytes=info.free_bytes,
        filesystem=info.filesystem,
        is_removable=info.is_removable,
        used_percent=round((info.used_bytes / info.total_bytes) * 100, 1) if info.total_bytes else 0.0,
    )


@router.post("/cleanup-now")
async def cleanup_now(request: Request, user: User = Depends(require_admin)):
    import asyncio

    await asyncio.get_event_loop().run_in_executor(None, request.app.state.storage.run_retention_pass)
    return {"ok": True}


# --------------------------------------------------------------------------
# Device formatting. Deliberately admin-only, deliberately requires the
# caller to echo the device path back as confirmation (see
# app/storage/device_format.py for the full safety rationale), and
# deliberately only ever offers ext4 as the target filesystem.
# --------------------------------------------------------------------------

class BlockDeviceOut(BaseModel):
    name: str
    path: str
    size_bytes: int
    fstype: str | None
    mountpoint: str | None
    type: str
    protected: bool
    protected_reason: str | None


class FormatDeviceRequest(BaseModel):
    device_path: str
    confirm: str
    filesystem: str = "ext4"


@router.get("/devices", response_model=list[BlockDeviceOut])
async def list_block_devices(user: User = Depends(require_admin)):
    """Lists all block devices/partitions on the system (mounted or not),
    for the Storage page's "format a new device" picker. Devices on the
    OS's own disk are flagged as protected and should be shown disabled,
    not offered as format targets, in the UI."""
    import asyncio

    try:
        devices = await asyncio.get_event_loop().run_in_executor(None, device_format.list_devices)
    except device_format.DeviceFormatError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [
        BlockDeviceOut(
            name=d.name, path=d.path, size_bytes=d.size_bytes, fstype=d.fstype,
            mountpoint=d.mountpoint, type=d.type, protected=d.protected,
            protected_reason=d.protected_reason,
        )
        for d in devices
    ]


@router.post("/devices/format")
async def format_block_device(payload: FormatDeviceRequest, user: User = Depends(require_admin)):
    """Formats a device as ext4. `confirm` must exactly match `device_path`
    -- this is intentionally not a simple "are you sure?" checkbox, since
    formatting destroys data irreversibly and this project would rather
    be annoying than responsible for an accidental wipe."""
    import asyncio

    try:
        await asyncio.get_event_loop().run_in_executor(
            None, device_format.format_device, payload.device_path, payload.confirm, payload.filesystem,
        )
    except device_format.DeviceFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
