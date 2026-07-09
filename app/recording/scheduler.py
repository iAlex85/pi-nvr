"""
Scheduled recording: cameras in RecordingMode.scheduled record only inside
configured day-of-week/time windows. Schedules are stored as JSON on disk
per-camera (config/schedules/<camera_id>.json) rather than new DB tables,
since they're simple structured data the Settings UI edits wholesale.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
from pathlib import Path

logger = logging.getLogger("pi_nvr.recording.scheduler")

SCHEDULE_DIR = Path("config/schedules")
CHECK_INTERVAL_SECONDS = 60

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _schedule_path(camera_id: int) -> Path:
    return SCHEDULE_DIR / f"{camera_id}.json"


def get_schedule(camera_id: int) -> list[dict]:
    """Returns a list of {day, start, end} windows, e.g.
    [{"day": "mon", "start": "08:00", "end": "18:00"}, ...]"""
    path = _schedule_path(camera_id)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def set_schedule(camera_id: int, windows: list[dict]) -> None:
    SCHEDULE_DIR.mkdir(parents=True, exist_ok=True)
    for w in windows:
        if w.get("day") not in WEEKDAYS:
            raise ValueError(f"Invalid day: {w.get('day')}")
        datetime.time.fromisoformat(w["start"])
        datetime.time.fromisoformat(w["end"])
    _schedule_path(camera_id).write_text(json.dumps(windows, indent=2))


def is_within_schedule(camera_id: int, now: datetime.datetime | None = None) -> bool:
    windows = get_schedule(camera_id)
    if not windows:
        return False
    now = now or datetime.datetime.now()
    day = WEEKDAYS[now.weekday()]
    current_time = now.time()
    for w in windows:
        if w["day"] != day:
            continue
        start = datetime.time.fromisoformat(w["start"])
        end = datetime.time.fromisoformat(w["end"])
        if start <= current_time <= end:
            return True
    return False


class ScheduleSupervisor:
    """Polls schedules once a minute and starts/stops recording on cameras
    in `scheduled` mode accordingly."""

    def __init__(self, recording_engine):
        self.engine = recording_engine
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        from app.database import session_scope
        from app.models import Camera, RecordingMode

        while self._running:
            try:
                with session_scope() as db:
                    scheduled_cameras = (
                        db.query(Camera)
                        .filter(Camera.recording_mode == RecordingMode.scheduled)
                        .filter(Camera.enabled.is_(True))
                        .all()
                    )
                    camera_ids = [c.id for c in scheduled_cameras]

                for camera_id in camera_ids:
                    should_record = is_within_schedule(camera_id)
                    currently_recording = self.engine.is_recording(camera_id)
                    if should_record and not currently_recording:
                        await self.engine.refresh_camera(camera_id)
                    elif not should_record and currently_recording:
                        await self.engine._stop_camera(camera_id)  # noqa: SLF001
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Schedule supervisor loop error")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
