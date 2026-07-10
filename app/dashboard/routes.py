from __future__ import annotations

import datetime
import logging
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.config import get_config
from app.database import get_db
from app.dashboard import system_stats
from app.models import Camera, EventLog, MotionEvent, User

logger = logging.getLogger("pi_nvr.dashboard")
router = APIRouter()


@router.get("/stats")
def stats(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cameras = db.query(Camera).all()
    today_start = datetime.datetime.combine(datetime.date.today(), datetime.time.min)
    motion_today = (
        db.query(MotionEvent).filter(MotionEvent.occurred_at >= today_start).count()
    )

    cam_mgr = request.app.state.camera_manager
    rec_engine = request.app.state.recording_engine
    online_count = sum(1 for c in cameras if (cam_mgr.get_status(c.id) or None) and cam_mgr.get_status(c.id).online)
    recording_count = sum(1 for c in cameras if rec_engine.is_recording(c.id))
    total_bitrate = sum(
        (cam_mgr.get_status(c.id).bitrate_kbps or 0)
        for c in cameras
        if cam_mgr.get_status(c.id)
    )

    snapshot = system_stats.full_snapshot()

    return {
        "system": snapshot,
        "cameras": {
            "total": len(cameras),
            "online": online_count,
            "recording": recording_count,
        },
        "motion_events_today": motion_today,
        "current_bitrate_kbps": round(total_bitrate, 1),
    }


@router.get("/logs")
def get_logs(
    category: str | None = None,
    level: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(EventLog)
    if category:
        query = query.filter(EventLog.category == category)
    if level:
        query = query.filter(EventLog.level == level)
    entries = query.order_by(EventLog.timestamp.desc()).limit(min(limit, 2000)).all()
    return [
        {
            "id": e.id,
            "timestamp": e.timestamp.isoformat(),
            "level": e.level,
            "category": e.category,
            "message": e.message,
            "camera_id": e.camera_id,
        }
        for e in entries
    ]


@router.get("/logs/download")
def download_logs(user: User = Depends(require_admin)):
    cfg = get_config()
    log_path = Path(cfg.get("logging.dir", "logs")) / "pi-nvr.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="No log file yet")
    return FileResponse(log_path, media_type="text/plain", filename="pi-nvr.log")


class SettingsUpdate(BaseModel):
    dotted_key: str
    value: object


@router.get("/settings")
def get_settings(user: User = Depends(require_admin)):
    return get_config().as_dict()


@router.put("/settings")
def update_setting(payload: SettingsUpdate, user: User = Depends(require_admin)):
    get_config().set(payload.dotted_key, payload.value)
    return {"ok": True}


@router.post("/restart-service")
def restart_service(user: User = Depends(require_admin)):
    """Asks systemd to restart the pi-nvr unit. No-ops safely under `uvicorn
    app.main:app` dev runs where systemctl either doesn't exist or the unit
    isn't registered."""
    try:
        subprocess.run(["systemctl", "restart", "pi-nvr"], check=True, timeout=10)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"Could not restart service: {exc}") from exc
    return {"ok": True}


@router.post("/restart-device")
def restart_device(user: User = Depends(require_admin)):
    try:
        subprocess.run(["sudo", "reboot"], check=True, timeout=10)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"Could not reboot: {exc}") from exc
    return {"ok": True}


@router.post("/shutdown-device")
def shutdown_device(user: User = Depends(require_admin)):
    try:
        subprocess.run(["sudo", "shutdown", "-h", "now"], check=True, timeout=10)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"Could not shut down: {exc}") from exc
    return {"ok": True}


@router.get("/backup")
def export_backup(user: User = Depends(require_admin)):
    """Bundles config.yaml + the SQLite DB into a single tarball the user
    can download from Settings > Backup."""
    import tarfile
    import tempfile

    cfg = get_config()
    db_path = Path(cfg.get("database.path", "database/pi-nvr.db"))

    tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    with tarfile.open(tmp.name, "w:gz") as tar:
        if cfg.path.exists():
            tar.add(cfg.path, arcname="config.yaml")
        if db_path.exists():
            tar.add(db_path, arcname="pi-nvr.db")

    return FileResponse(
        tmp.name,
        media_type="application/gzip",
        filename=f"pi-nvr-backup-{datetime.date.today().isoformat()}.tar.gz",
    )


@router.post("/restore")
async def restore_backup(file: UploadFile, user: User = Depends(require_admin)):
    """Restores config.yaml + DB from a backup tarball. Requires a service
    restart afterward to pick up the restored DB connection cleanly."""
    import tarfile
    import tempfile

    cfg = get_config()
    db_path = Path(cfg.get("database.path", "database/pi-nvr.db"))

    with tempfile.NamedTemporaryFile(suffix=".tar.gz") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp.flush()

        try:
            with tarfile.open(tmp.name, "r:gz") as tar:
                names = tar.getnames()
                if "config.yaml" not in names and "pi-nvr.db" not in names:
                    raise HTTPException(status_code=400, detail="Backup archive missing expected files")

                extract_dir = tempfile.mkdtemp()
                tar.extractall(extract_dir, filter="data")

                extracted_config = Path(extract_dir) / "config.yaml"
                extracted_db = Path(extract_dir) / "pi-nvr.db"

                if extracted_config.exists():
                    shutil.copy(extracted_config, cfg.path)
                if extracted_db.exists():
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(extracted_db, db_path)
        except tarfile.TarError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid backup archive: {exc}") from exc

    return {"ok": True, "restart_required": True}
