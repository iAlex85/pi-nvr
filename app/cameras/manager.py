"""
CameraManager owns the *runtime* state of each configured camera: is it
reachable, what's its last-seen FPS/bitrate, etc. Persistent camera config
(URL, name, credentials...) lives in the `cameras` DB table via normal CRUD
in app/cameras/routes.py -- this module is the live supervisor on top of
that data.

Reconnection strategy: a background asyncio task per enabled camera probes
the RTSP URL periodically (cheap `ffprobe`-style connect, not a full pull)
and flips online/offline state. RecordingEngine and MotionSupervisor watch
that state to know when to (re)start their own subprocesses.

Many budget/consumer cameras (this includes most "generic Chinese WiFi
camera" hardware) only accept ONE RTSP client at a time -- a second
connection attempt doesn't queue, it just fails or knocks the first one
loose. This matters a lot here: if this probe reconnects on its own timer
while RecordingEngine or live view already holds the camera's one
available slot, it can intermittently break an otherwise-working stream
for no reason. To avoid that, the probe is skipped for any camera the
RecordingEngine already confirms is actively recording -- an active
recording connection is itself conclusive proof the camera is online, so
a second, redundant probe connection is both unnecessary and actively
harmful on single-client hardware.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time

from app.cameras.url_utils import build_authenticated_rtsp_url
from app.config import Config
from app.database import session_scope
from app.models import Camera

logger = logging.getLogger("pi_nvr.cameras")

# Default probe interval. Kept fairly quick now that the recording-skip
# optimization below (see _probe_once) already prevents the specific
# contention scenario that motivated a much slower default previously --
# a probe running concurrently with an active recording connection on a
# single-RTSP-client camera. With that handled separately, polling faster
# for cameras that *aren't* currently recording is safe and gives much
# quicker offline/online detection. Override via cameras.probe_interval_seconds.
DEFAULT_PROBE_INTERVAL_SECONDS = 20
PROBE_TIMEOUT_SECONDS = 8


@dataclasses.dataclass
class CameraStatus:
    camera_id: int
    online: bool = False
    last_seen: float | None = None
    last_error: str | None = None
    fps: float | None = None
    bitrate_kbps: float | None = None
    consecutive_failures: int = 0


class CameraManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._status: dict[int, CameraStatus] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._running = False
        # Set post-construction from app/main.py's lifespan, once
        # RecordingEngine/NotificationManager exist. Optional by design --
        # CameraManager must still work (e.g. in tests) without them wired up.
        self._recording_engine = None
        self._notifications = None

    def set_recording_engine(self, recording_engine) -> None:
        self._recording_engine = recording_engine

    def set_notifications(self, notifications) -> None:
        self._notifications = notifications

    @property
    def _probe_interval(self) -> float:
        return self.cfg.get("cameras.probe_interval_seconds", DEFAULT_PROBE_INTERVAL_SECONDS)

    async def start(self) -> None:
        self._running = True
        with session_scope() as db:
            cameras = db.query(Camera).filter(Camera.enabled.is_(True)).all()
            camera_ids = [c.id for c in cameras]
        for cam_id in camera_ids:
            self._spawn_probe_task(cam_id)
        logger.info("CameraManager started, watching %d camera(s)", len(camera_ids))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    def _spawn_probe_task(self, camera_id: int) -> None:
        if camera_id in self._tasks:
            return
        self._status.setdefault(camera_id, CameraStatus(camera_id=camera_id))
        self._tasks[camera_id] = asyncio.create_task(self._probe_loop(camera_id))

    async def watch_camera(self, camera_id: int) -> None:
        """Called by routes.py after a camera is created/enabled."""
        self._spawn_probe_task(camera_id)

    async def unwatch_camera(self, camera_id: int) -> None:
        task = self._tasks.pop(camera_id, None)
        if task:
            task.cancel()
        self._status.pop(camera_id, None)

    def get_status(self, camera_id: int) -> CameraStatus | None:
        return self._status.get(camera_id)

    def all_status(self) -> dict[int, CameraStatus]:
        return dict(self._status)

    async def _probe_loop(self, camera_id: int) -> None:
        while self._running:
            try:
                await self._probe_once(camera_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                logger.exception("Unexpected error probing camera %s: %s", camera_id, exc)
            await asyncio.sleep(self._probe_interval)

    async def _probe_once(self, camera_id: int) -> None:
        if self._recording_engine is not None and self._recording_engine.is_recording(camera_id):
            # Already have conclusive proof of connectivity from an active
            # recording connection -- skip the redundant probe rather than
            # risk contending for a single-RTSP-client camera's one slot.
            status = self._status.setdefault(camera_id, CameraStatus(camera_id=camera_id))
            status.online = True
            status.last_seen = time.time()
            status.last_error = None
            status.consecutive_failures = 0
            return

        with session_scope() as db:
            camera = db.get(Camera, camera_id)
            if camera is None or not camera.enabled:
                await self.unwatch_camera(camera_id)
                return
            rtsp_url = build_authenticated_rtsp_url(camera)
            camera_name = camera.name

        status = self._status.setdefault(camera_id, CameraStatus(camera_id=camera_id))

        # `ffprobe` opens the stream just far enough to read stream info,
        # then exits -- much cheaper than pulling frames continuously.
        cmd = [
            "ffprobe",
            "-v", "error",
            "-rtsp_transport", "tcp",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,bit_rate",
            "-of", "default=noprint_wrappers=1",
            "-timeout", str(PROBE_TIMEOUT_SECONDS * 1_000_000),
            rtsp_url,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=PROBE_TIMEOUT_SECONDS + 3
            )
        except asyncio.TimeoutError:
            self._mark_offline(status, "probe timed out", camera_name)
            return

        if proc.returncode != 0:
            self._mark_offline(status, stderr.decode(errors="replace")[:200], camera_name)
            return

        was_offline = not status.online
        status.online = True
        status.last_seen = time.time()
        status.last_error = None
        status.consecutive_failures = 0
        if was_offline:
            self._notify(camera_id, "camera_online", camera_name)

        for line in stdout.decode(errors="replace").splitlines():
            if line.startswith("r_frame_rate="):
                status.fps = _parse_frame_rate(line.split("=", 1)[1])
            elif line.startswith("bit_rate=") and line.split("=", 1)[1].isdigit():
                status.bitrate_kbps = int(line.split("=", 1)[1]) / 1000

    def _mark_offline(self, status: CameraStatus, error: str, camera_name: str = "") -> None:
        was_online = status.online
        status.online = False
        status.last_error = error
        status.consecutive_failures += 1
        if was_online:
            logger.warning(
                "Camera %s went offline: %s", status.camera_id, error
            )
            self._notify(status.camera_id, "camera_offline", camera_name)

    def _notify(self, camera_id: int, event_type: str, camera_name: str) -> None:
        if self._notifications is None:
            return
        asyncio.create_task(
            self._notifications.publish(event_type, {"camera_id": camera_id, "camera_name": camera_name})
        )


def _parse_frame_rate(raw: str) -> float | None:
    try:
        if "/" in raw:
            num, den = raw.split("/")
            den = float(den)
            return round(float(num) / den, 2) if den else None
        return float(raw)
    except (ValueError, ZeroDivisionError):
        return None
