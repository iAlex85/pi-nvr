"""
Motion detection, kept deliberately cheap for a Pi 3:

  - Pulls frames via OpenCV's FFmpeg-backed VideoCapture, but points it at
    the camera's *substream* (rtsp_substream_url) when the camera exposes
    one -- most ONVIF cameras provide a low-res secondary stream exactly
    for this purpose. Falls back to decoding the main stream at a reduced
    sample rate if no substream is configured.
  - Frames are resized to `motion.downscale_width` x `motion.downscale_height`
    (default 320x180) and converted to grayscale before background
    subtraction (cv2.createBackgroundSubtractorMOG2), which is an order of
    magnitude cheaper than running subtraction at full resolution.
  - Sampling is rate-limited to `motion.sample_fps` (default 5) rather than
    processing every decoded frame.
  - Include/exclude polygon zones are applied as a mask over the diff image
    before contour detection, so motion in "ignore" areas (e.g. a street
    visible in frame) never triggers events.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time

import cv2
import numpy as np

from app.config import Config
from app.database import session_scope
from app.models import Camera, MotionEvent, MotionZone, RecordingMode

logger = logging.getLogger("pi_nvr.motion")


@dataclasses.dataclass
class MotionState:
    camera_id: int
    last_motion_at: float | None = None
    last_score: float = 0.0
    task: asyncio.Task | None = None


class MotionDetectorWorker:
    """Runs in a background asyncio task (frame reads happen in a thread
    executor since cv2.VideoCapture.read() is blocking)."""

    def __init__(self, cfg: Config, camera: Camera, on_motion):
        self.cfg = cfg
        self.camera = camera
        self.on_motion = on_motion
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=200, varThreshold=16, detectShadows=False
        )
        self._running = False
        self._zone_mask: np.ndarray | None = None

    def _stream_url(self) -> str:
        return self.camera.rtsp_substream_url or self.camera.rtsp_url

    def _load_zones(self, width: int, height: int) -> np.ndarray:
        """Builds a single-channel mask: 255 where motion should be
        considered, 0 where it should be ignored. If only 'exclude' zones
        exist, everything outside them is included by default; if any
        'include' zone exists, only those areas count."""
        with session_scope() as db:
            zones = db.query(MotionZone).filter(MotionZone.camera_id == self.camera.id).all()
            zone_data = [(z.zone_type, json.loads(z.points_json)) for z in zones]

        mask = np.full((height, width), 255, dtype=np.uint8)
        includes = [pts for zt, pts in zone_data if zt == "include"]
        excludes = [pts for zt, pts in zone_data if zt == "exclude"]

        if includes:
            mask[:] = 0
            for pts in includes:
                poly = np.array([[int(x * width), int(y * height)] for x, y in pts], dtype=np.int32)
                cv2.fillPoly(mask, [poly], 255)
        for pts in excludes:
            poly = np.array([[int(x * width), int(y * height)] for x, y in pts], dtype=np.int32)
            cv2.fillPoly(mask, [poly], 0)
        return mask

    async def run(self) -> None:
        self._running = True
        loop = asyncio.get_event_loop()
        width = self.cfg.get("motion.downscale_width", 320)
        height = self.cfg.get("motion.downscale_height", 180)
        sample_fps = max(self.cfg.get("motion.sample_fps", 5), 1)
        min_interval = 1.0 / sample_fps
        self._zone_mask = self._load_zones(width, height)

        cap = None
        try:
            cap = await loop.run_in_executor(None, self._open_capture)
            if cap is None or not cap.isOpened():
                logger.warning("Motion detector: could not open stream for camera %s", self.camera.id)
                return

            last_sample = 0.0
            while self._running:
                now = time.monotonic()
                if now - last_sample < min_interval:
                    await asyncio.sleep(min_interval - (now - last_sample))
                    continue
                last_sample = now

                ok, frame = await loop.run_in_executor(None, cap.read)
                if not ok or frame is None:
                    logger.debug("Motion detector: frame read failed for camera %s, retrying", self.camera.id)
                    await asyncio.sleep(1)
                    continue

                await loop.run_in_executor(None, self._process_frame, frame, width, height)
        except asyncio.CancelledError:
            raise
        finally:
            if cap is not None:
                cap.release()

    def _open_capture(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self._stream_url())
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _process_frame(self, frame: np.ndarray, width: int, height: int) -> None:
        small = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        fg_mask = self._bg_subtractor.apply(gray)
        _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)

        if self._zone_mask is not None:
            fg_mask = cv2.bitwise_and(fg_mask, self._zone_mask)

        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frame_area = width * height
        min_pct = self.cfg.get("motion.min_object_area_percent", 0.5) / 100.0
        max_pct = self.cfg.get("motion.max_object_area_percent", 80) / 100.0

        best_box = None
        best_area = 0
        for contour in contours:
            area = cv2.contourArea(contour)
            area_pct = area / frame_area
            if area_pct < min_pct or area_pct > max_pct:
                continue
            if area > best_area:
                best_area = area
                best_box = cv2.boundingRect(contour)

        if best_box is not None:
            score = round((best_area / frame_area) * 100, 2)
            self.on_motion(self.camera.id, score, best_box)

    def stop(self) -> None:
        self._running = False


class MotionSupervisor:
    def __init__(self, cfg: Config, recording_engine, notifications):
        self.cfg = cfg
        self.recording_engine = recording_engine
        self.notifications = notifications
        self._workers: dict[int, MotionDetectorWorker] = {}
        self._states: dict[int, MotionState] = {}
        self._running = False

    async def start(self, camera_manager) -> None:
        self._running = True
        with session_scope() as db:
            cameras = (
                db.query(Camera)
                .filter(Camera.enabled.is_(True))
                .filter(Camera.motion_enabled.is_(True))
                .all()
            )
            for camera in cameras:
                await self._start_worker(camera)
        logger.info("MotionSupervisor started, watching %d camera(s)", len(self._workers))

    async def stop(self) -> None:
        self._running = False
        for camera_id in list(self._workers.keys()):
            await self._stop_worker(camera_id)

    async def _start_worker(self, camera: Camera) -> None:
        if camera.id in self._workers:
            return
        state = self._states.setdefault(camera.id, MotionState(camera_id=camera.id))
        worker = MotionDetectorWorker(self.cfg, camera, self._handle_motion_sync)
        self._workers[camera.id] = worker
        state.task = asyncio.create_task(worker.run())

    async def _stop_worker(self, camera_id: int) -> None:
        worker = self._workers.pop(camera_id, None)
        if worker:
            worker.stop()
        state = self._states.get(camera_id)
        if state and state.task:
            state.task.cancel()

    async def refresh_camera(self, camera_id: int) -> None:
        await self._stop_worker(camera_id)
        with session_scope() as db:
            camera = db.get(Camera, camera_id)
            if camera and camera.enabled and camera.motion_enabled:
                await self._start_worker(camera)

    def _handle_motion_sync(self, camera_id: int, score: float, bbox: tuple[int, int, int, int]) -> None:
        """Called from a worker-thread executor callback -- schedule the
        actual (async) handling onto the event loop."""
        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(self._handle_motion(camera_id, score, bbox), loop)

    async def _handle_motion(self, camera_id: int, score: float, bbox: tuple[int, int, int, int]) -> None:
        state = self._states.setdefault(camera_id, MotionState(camera_id=camera_id))
        cooldown = self.cfg.get("motion.cooldown_seconds", 10)
        now = time.time()
        if state.last_motion_at and (now - state.last_motion_at) < cooldown:
            return
        state.last_motion_at = now
        state.last_score = score

        with session_scope() as db:
            event = MotionEvent(
                camera_id=camera_id,
                score=score,
                bounding_box=",".join(str(v) for v in bbox),
            )
            db.add(event)
            camera = db.get(Camera, camera_id)
            camera_name = camera.name if camera else str(camera_id)
            recording_mode = camera.recording_mode if camera else None

        logger.info("Motion detected on camera %s (score=%.2f)", camera_id, score)

        if recording_mode == RecordingMode.motion:
            await self.recording_engine.handle_motion(camera_id)

        await self.notifications.publish(
            "motion",
            {"camera_id": camera_id, "camera_name": camera_name, "score": score},
        )
