from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_admin, get_current_user
from app.cameras import onvif_discovery, network_scan, ptz as ptz_mod
from app.cameras.url_utils import build_authenticated_rtsp_url
from app.cameras.crypto import encrypt
from app.database import get_db
from app.models import Camera, CameraProtocol, PTZPreset, RecordingMode, User

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
    rtsp_substream_url: str | None
    username: str | None
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


@router.post("/scan-network")
async def scan_network(user: User = Depends(require_admin)):
    """Fallback for cameras that don't answer ONVIF WS-Discovery (common
    with budget consumer cameras that only support a vendor's proprietary
    app-pairing protocol). Sweeps the Pi's LAN subnet(s) for hosts with
    camera-typical ports open (RTSP/ONVIF/HTTP) so at least the *IP* is
    known, even if the exact stream path still needs to be found manually.
    Can take several seconds on a full /24."""
    results = await network_scan.scan_for_cameras()
    return {"hosts": results}


# --------------------------------------------------------------------------
# Live view (MJPEG). This is the one place the software *does* decode
# video continuously -- there's no way to show a live low-latency preview
# in a plain <img>/<video> tag from a stream-copied recording. Costly, so
# it's meant for a small number of concurrent viewers, not the recording
# path itself, and it only runs while a browser tab has it open.
#
# Cameras that only accept a single RTSP client (common on budget/
# consumer hardware) make orphaned streams expensive: if a previous
# live-view connection isn't cleanly torn down when a browser tab
# navigates away, it can occupy the camera's one connection slot
# indefinitely, leaving a *new* live-view request unable to connect at
# all. Detecting "the client is gone" purely from the server side
# (`request.is_disconnected()`) isn't fully reliable across browsers for
# a multipart/x-mixed-replace stream, so instead of relying on that
# alone, we track the active process per camera and proactively kill any
# previous one the moment a new live-view request for that same camera
# comes in -- guaranteeing at most one live-view stream per camera
# regardless of whether the old client's disconnect was ever detected.
# --------------------------------------------------------------------------

import asyncio as _asyncio  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402

_active_mjpeg_processes: dict[int, "_asyncio.subprocess.Process"] = {}
# Serializes the kill-old/settle/spawn-new sequence per camera. Without
# this, two requests arriving close together (e.g. clicking through
# live-view layout options quickly) could interleave: request B reads
# _active_mjpeg_processes before request A has finished killing its old
# process and registering its new one, so B ends up killing the wrong
# process or spawning a second connection concurrently with A's --
# exactly the kind of overlap a single-RTSP-client camera can't handle.
_mjpeg_locks: dict[int, "_asyncio.Lock"] = {}


def _get_mjpeg_lock(camera_id: int) -> "_asyncio.Lock":
    lock = _mjpeg_locks.get(camera_id)
    if lock is None:
        lock = _asyncio.Lock()
        _mjpeg_locks[camera_id] = lock
    return lock


async def _kill_process(proc) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await _asyncio.wait_for(proc.wait(), timeout=3)
    except _asyncio.TimeoutError:
        proc.kill()


async def _spawn_mjpeg_process(camera_id: int, cmd: list, attempts: int = 3, retry_delay: float = 2.0):
    """Starts the ffmpeg live-view process, retrying a few times if it
    exits almost immediately -- typically means the camera hasn't yet
    released a just-closed connection internally (its own session
    cleanup can lag behind our process actually exiting), so an instant
    reconnect gets rejected even though nothing is actually wrong."""
    last_proc = None
    for attempt in range(1, attempts + 1):
        logger.info("Live view: spawning ffmpeg for camera %s (attempt %d/%d)", camera_id, attempt, attempts)
        proc = await _asyncio.create_subprocess_exec(
            *cmd, stdout=_asyncio.subprocess.PIPE, stderr=_asyncio.subprocess.PIPE
        )
        try:
            await _asyncio.wait_for(proc.wait(), timeout=1.5)
            stderr = b""
            if proc.stderr:
                stderr = await proc.stderr.read()
            logger.warning(
                "Live view: ffmpeg for camera %s exited almost immediately "
                "(attempt %d/%d, code=%s): %s",
                camera_id, attempt, attempts, proc.returncode,
                stderr.decode(errors="replace")[-500:],
            )
            last_proc = proc
            if attempt < attempts:
                await _asyncio.sleep(retry_delay)
                continue
        except _asyncio.TimeoutError:
            logger.info("Live view: ffmpeg for camera %s connected successfully (attempt %d/%d)", camera_id, attempt, attempts)
            return proc
    logger.error("Live view: all %d connection attempts failed for camera %s", attempts, camera_id)
    return last_proc


@router.get("/{camera_id}/mjpeg")
async def mjpeg_stream(camera_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    logger.info("Live view: new request for camera %s", camera_id)

    lock = _get_mjpeg_lock(camera_id)
    async with lock:
        old_proc = _active_mjpeg_processes.get(camera_id)
        if old_proc is not None:
            logger.info("Live view: killing previous stream for camera %s (pid=%s)", camera_id, old_proc.pid)
            await _kill_process(old_proc)
            # Our process exiting promptly doesn't mean the camera's own
            # firmware has released its RTSP session slot equally promptly --
            # give it a moment before trying to reconnect.
            await _asyncio.sleep(1.5)

        rtsp_url = build_authenticated_rtsp_url(camera, substream=True)

        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-i", rtsp_url,
            "-f", "mjpeg", "-q:v", "6", "-r", "8",
            "-vf", "scale=640:-2",
            "pipe:1",
        ]
        proc = await _spawn_mjpeg_process(camera_id, cmd)
        _active_mjpeg_processes[camera_id] = proc

    boundary = "pi-nvr-frame"

    async def frame_generator():
        try:
            buffer = b""
            while True:
                if await request.is_disconnected():
                    # The browser navigated away or closed the tab. Without
                    # this check, we'd just keep blocking on the next
                    # ffmpeg read below and never notice -- leaving this
                    # process (and its one RTSP connection slot on cameras
                    # that only support a single client) orphaned
                    # indefinitely, until a new live-view request shows up
                    # and can't get a connection because the old one never
                    # let go.
                    break
                try:
                    chunk = await _asyncio.wait_for(proc.stdout.read(4096), timeout=1.0)
                except _asyncio.TimeoutError:
                    continue  # no new data yet -- loop back and re-check disconnect status
                if not chunk:
                    break
                buffer += chunk
                # JPEG frames are delimited by SOI (FFD8) / EOI (FFD9) markers.
                while True:
                    start = buffer.find(b"\xff\xd8")
                    end = buffer.find(b"\xff\xd9")
                    if start == -1 or end == -1 or end < start:
                        break
                    frame = buffer[start:end + 2]
                    buffer = buffer[end + 2:]
                    yield (
                        f"--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n"
                    ).encode() + frame + b"\r\n"
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await _asyncio.wait_for(proc.wait(), timeout=3)
                except _asyncio.TimeoutError:
                    proc.kill()
            if _active_mjpeg_processes.get(camera_id) is proc:
                del _active_mjpeg_processes[camera_id]

    return StreamingResponse(
        frame_generator(),
        media_type=f"multipart/x-mixed-replace; boundary={boundary}",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


def dataclasses_asdict_safe(obj) -> dict:
    import dataclasses

    return dataclasses.asdict(obj)


# --------------------------------------------------------------------------
# PTZ controls (Phase 9). All PTZ endpoints require the camera to have
# ONVIF host/credentials configured; unsupported cameras get a 409.
# --------------------------------------------------------------------------

class PTZMoveRequest(BaseModel):
    direction: str  # up/down/left/right/zoom_in/zoom_out
    speed: float = 0.5


class PTZPresetIn(BaseModel):
    name: str


class PTZPresetOut(BaseModel):
    id: int
    name: str
    onvif_token: str | None

    class Config:
        from_attributes = True


def _get_camera_or_404(db: Session, camera_id: int) -> Camera:
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera


@router.post("/{camera_id}/ptz/detect")
async def detect_ptz_support(camera_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    camera = _get_camera_or_404(db, camera_id)
    supported = await ptz_mod.get_capabilities(camera)
    camera.supports_ptz = supported
    db.add(camera)
    return {"supports_ptz": supported}


@router.post("/{camera_id}/ptz/move")
async def ptz_move(camera_id: int, payload: PTZMoveRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    camera = _get_camera_or_404(db, camera_id)
    try:
        await ptz_mod.move(camera, payload.direction, payload.speed)
    except ptz_mod.PTZUnsupportedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/{camera_id}/ptz/stop")
async def ptz_stop(camera_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    camera = _get_camera_or_404(db, camera_id)
    try:
        await ptz_mod.stop(camera)
    except ptz_mod.PTZUnsupportedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/{camera_id}/ptz/home")
async def ptz_home(camera_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    camera = _get_camera_or_404(db, camera_id)
    try:
        await ptz_mod.go_home(camera)
    except ptz_mod.PTZUnsupportedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/{camera_id}/ptz/presets", response_model=list[PTZPresetOut])
def list_ptz_presets(camera_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _get_camera_or_404(db, camera_id)
    return db.query(PTZPreset).filter(PTZPreset.camera_id == camera_id).all()


@router.post("/{camera_id}/ptz/presets", response_model=PTZPresetOut)
async def create_ptz_preset(camera_id: int, payload: PTZPresetIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    camera = _get_camera_or_404(db, camera_id)
    try:
        token = await ptz_mod.set_preset(camera, payload.name)
    except ptz_mod.PTZUnsupportedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    preset = PTZPreset(camera_id=camera_id, name=payload.name, onvif_token=token)
    db.add(preset)
    db.flush()
    return preset


@router.post("/{camera_id}/ptz/presets/{preset_id}/goto")
async def goto_ptz_preset(camera_id: int, preset_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    camera = _get_camera_or_404(db, camera_id)
    preset = db.get(PTZPreset, preset_id)
    if preset is None or preset.camera_id != camera_id:
        raise HTTPException(status_code=404, detail="Preset not found")
    try:
        await ptz_mod.goto_preset(camera, preset.onvif_token)
    except ptz_mod.PTZUnsupportedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/{camera_id}/ptz/presets/{preset_id}")
def delete_ptz_preset(camera_id: int, preset_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    preset = db.get(PTZPreset, preset_id)
    if preset is None or preset.camera_id != camera_id:
        raise HTTPException(status_code=404, detail="Preset not found")
    db.delete(preset)
    return {"ok": True}
