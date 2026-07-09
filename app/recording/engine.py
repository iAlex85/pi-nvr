"""
RecordingEngine drives one ffmpeg subprocess per actively-recording camera.

Design principles (see docs/ARCHITECTURE.md):
  - Continuous mode: a single long-running `ffmpeg -c copy -f segment`
    process per camera. No decode, no re-encode -- just remuxing packets
    into rotating segment files. This is what makes multi-camera recording
    viable on a Pi 3.
  - Motion mode: a *ring buffer* of short segments is always being written
    (cheap, stream-copy). When MotionSupervisor reports motion, the engine
    marks the currently-writing + previous ring segment as "keep" (covers
    the pre-record window) and lets the segmenter run for
    `post_record_seconds` longer before returning to ring-only writes that
    get overwritten. This gets pre/post buffering without frame-accurate
    ring-buffer bookkeeping in Python, at the cost of the buffer being
    segment-granular rather than frame-granular.
  - Overlay (timestamp/camera name) requires decoding+encoding the video
    stream, which costs real CPU. It is opt-in per the config and, when
    enabled, uses `-c:v libx264 -preset ultrafast -tune zerolatency` to
    keep the cost as low as possible; audio is still stream-copied.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import shlex
from pathlib import Path

from app.config import Config
from app.cameras.crypto import decrypt
from app.database import session_scope
from app.models import Camera, Recording, RecordingMode

logger = logging.getLogger("pi_nvr.recording")

RING_BUFFER_SEGMENTS = 6  # ring depth for motion pre-record capture


class _CameraRecordingState:
    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self.process: asyncio.subprocess.Process | None = None
        self.mode: RecordingMode = RecordingMode.off
        self.output_dir: Path | None = None
        self.watcher_task: asyncio.Task | None = None
        self.motion_extend_until: float = 0.0
        self.current_recording_db_id: int | None = None


class RecordingEngine:
    def __init__(self, cfg: Config, storage_manager):
        self.cfg = cfg
        self.storage = storage_manager
        self._states: dict[int, _CameraRecordingState] = {}
        self._running = False

    async def start(self, camera_manager) -> None:
        self._running = True
        self._camera_manager = camera_manager
        with session_scope() as db:
            cameras = db.query(Camera).filter(Camera.enabled.is_(True)).all()
            for camera in cameras:
                if camera.recording_mode in (RecordingMode.continuous, RecordingMode.motion):
                    await self._start_camera(camera.id)
        logger.info("RecordingEngine started")

    async def stop(self) -> None:
        self._running = False
        for state in list(self._states.values()):
            await self._stop_camera(state.camera_id)

    async def refresh_camera(self, camera_id: int) -> None:
        """Call after a camera's config changes to restart its pipeline
        with the new settings."""
        await self._stop_camera(camera_id)
        with session_scope() as db:
            camera = db.get(Camera, camera_id)
            if camera and camera.enabled and camera.recording_mode != RecordingMode.off:
                await self._start_camera(camera_id)

    async def _start_camera(self, camera_id: int) -> None:
        with session_scope() as db:
            camera = db.get(Camera, camera_id)
            if camera is None:
                return
            output_dir = self.storage.recording_dir_for_camera(camera)
            mode = camera.recording_mode
            rtsp_url = self._auth_url(camera)

        output_dir.mkdir(parents=True, exist_ok=True)
        state = self._states.setdefault(camera_id, _CameraRecordingState(camera_id))
        state.mode = mode
        state.output_dir = output_dir

        cmd = self._build_ffmpeg_command(camera_id, rtsp_url, output_dir, mode)
        logger.info("Starting recording for camera %s: %s", camera_id, shlex.join(cmd))

        state.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        state.watcher_task = asyncio.create_task(self._watch_process(camera_id))

    async def _stop_camera(self, camera_id: int) -> None:
        state = self._states.get(camera_id)
        if state is None:
            return
        if state.process and state.process.returncode is None:
            state.process.terminate()
            try:
                await asyncio.wait_for(state.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                state.process.kill()
        if state.watcher_task:
            state.watcher_task.cancel()
        self._states.pop(camera_id, None)

    async def _watch_process(self, camera_id: int) -> None:
        """Restart the ffmpeg process with backoff if it dies unexpectedly
        (camera dropped connection, network blip, etc.)."""
        backoff = 2
        while self._running:
            state = self._states.get(camera_id)
            if state is None or state.process is None:
                return
            returncode = await state.process.wait()
            if not self._running:
                return
            stderr = b""
            if state.process.stderr:
                stderr = await state.process.stderr.read()
            logger.warning(
                "Recording process for camera %s exited (code=%s): %s",
                camera_id, returncode, stderr.decode(errors="replace")[-500:],
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
            with session_scope() as db:
                camera = db.get(Camera, camera_id)
                if camera is None or not camera.enabled or camera.recording_mode == RecordingMode.off:
                    return
            await self._start_camera(camera_id)
            return  # _start_camera spawns a fresh watcher task

    def _auth_url(self, camera: Camera) -> str:
        """Inject basic-auth credentials into the RTSP URL if the camera
        has stored username/password and the URL doesn't already embed
        credentials."""
        if "@" in camera.rtsp_url.split("://", 1)[-1]:
            return camera.rtsp_url
        password = decrypt(camera.password_enc)
        if not camera.username or not password:
            return camera.rtsp_url
        scheme, rest = camera.rtsp_url.split("://", 1)
        return f"{scheme}://{camera.username}:{password}@{rest}"

    def _build_ffmpeg_command(
        self, camera_id: int, rtsp_url: str, output_dir: Path, mode: RecordingMode
    ) -> list[str]:
        container = self.cfg.get("recording.container", "mp4")
        segment_seconds = self.cfg.get("recording.segment_seconds", 600)
        overlay_ts = self.cfg.get("recording.overlay.timestamp", True)
        overlay_name = self.cfg.get("recording.overlay.camera_name", True)

        pattern = str(output_dir / f"cam{camera_id}_%Y%m%d_%H%M%S.{container}")

        cmd = ["ffmpeg", "-nostdin", "-loglevel", "warning", "-rtsp_transport", "tcp", "-i", rtsp_url]

        if overlay_ts or overlay_name:
            drawtext = self._overlay_filter(camera_id, overlay_ts, overlay_name)
            cmd += ["-vf", drawtext, "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency"]
        else:
            cmd += ["-c:v", "copy"]

        cmd += ["-c:a", "copy"]

        if mode == RecordingMode.motion:
            # Ring buffer: small, fixed number of segments, each roughly
            # pre_record_seconds long, wrapping so old ones get overwritten
            # unless _keep_segment() has renamed them out of the ring dir.
            pre_seconds = self.cfg.get("recording.pre_record_seconds", 5)
            ring_seconds = max(pre_seconds, 5)
            cmd += [
                "-f", "segment",
                "-segment_time", str(ring_seconds),
                "-segment_wrap", str(RING_BUFFER_SEGMENTS),
                "-reset_timestamps", "1",
                "-strftime", "1",
                str(output_dir / f"cam{camera_id}_ring_%Y%m%d_%H%M%S.{container}"),
            ]
        else:
            cmd += [
                "-f", "segment",
                "-segment_time", str(segment_seconds),
                "-reset_timestamps", "1",
                "-strftime", "1",
                pattern,
            ]

        return cmd

    def _overlay_filter(self, camera_id: int, ts: bool, name: bool) -> str:
        parts = []
        if name:
            with session_scope() as db:
                camera = db.get(Camera, camera_id)
                cam_name = (camera.name if camera else f"Camera {camera_id}").replace(":", "")
            parts.append(
                f"drawtext=text='{cam_name}':x=10:y=10:fontsize=18:fontcolor=white:"
                f"box=1:boxcolor=black@0.4"
            )
        if ts:
            parts.append(
                "drawtext=text='%{localtime}':x=10:y=H-30:fontsize=18:fontcolor=white:"
                "box=1:boxcolor=black@0.4"
            )
        return ",".join(parts)

    async def handle_motion(self, camera_id: int) -> None:
        """Called by MotionSupervisor when motion is detected on a camera
        in `motion` recording mode. Extends the "keep window" so the ring
        segments covering this event survive rotation, covering the
        pre_record/post_record window configured in settings."""
        state = self._states.get(camera_id)
        if state is None or state.mode != RecordingMode.motion:
            return
        post_seconds = self.cfg.get("recording.post_record_seconds", 10)
        loop = asyncio.get_event_loop()
        state.motion_extend_until = loop.time() + post_seconds
        logger.info("Motion recording window extended for camera %s (+%ss)", camera_id, post_seconds)
        asyncio.create_task(self._keep_ring_segments(camera_id))

    async def _keep_ring_segments(self, camera_id: int) -> None:
        """Copy the current ring segment(s) out of the wrap-around pool
        into a permanent filename and register a Recording row, so a
        motion event's footage isn't overwritten by the ring buffer."""
        state = self._states.get(camera_id)
        if state is None or state.output_dir is None:
            return
        ring_files = sorted(state.output_dir.glob(f"cam{camera_id}_ring_*"))
        if not ring_files:
            return
        latest = ring_files[-1]
        permanent_dir = state.output_dir
        permanent_name = latest.name.replace("_ring_", "_motion_")
        permanent_path = permanent_dir / permanent_name
        try:
            if not permanent_path.exists():
                permanent_path.write_bytes(latest.read_bytes())
        except OSError as exc:
            logger.error("Failed to preserve motion segment for camera %s: %s", camera_id, exc)
            return

        with session_scope() as db:
            rec = Recording(
                camera_id=camera_id,
                file_path=str(permanent_path),
                started_at=datetime.datetime.now(datetime.timezone.utc),
                trigger="motion",
            )
            db.add(rec)

    def is_recording(self, camera_id: int) -> bool:
        state = self._states.get(camera_id)
        return bool(state and state.process and state.process.returncode is None)

    async def start_manual(self, camera_id: int) -> None:
        with session_scope() as db:
            camera = db.get(Camera, camera_id)
            if camera:
                camera.recording_mode = RecordingMode.continuous
        await self.refresh_camera(camera_id)

    async def stop_manual(self, camera_id: int) -> None:
        with session_scope() as db:
            camera = db.get(Camera, camera_id)
            if camera:
                camera.recording_mode = RecordingMode.off
        await self._stop_camera(camera_id)
