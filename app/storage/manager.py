"""
StorageManager resolves *where on disk* each camera's recordings/snapshots
live, reports disk usage/health for the Settings > Storage page, and runs
a periodic retention/cleanup pass (age-based and usage%-based).

Storage "targets" (local path / USB / network mount) are rows in the
storage_targets table; this module is the runtime layer that turns a
target + camera into actual directories, and enforces the retention policy
configured in config.yaml.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config import Config
from app.database import session_scope
from app.models import Camera, Recording, StorageTarget

logger = logging.getLogger("pi_nvr.storage")

CANDIDATE_MOUNT_ROOTS = ["/media", "/mnt", "/run/media"]


@dataclass
class MountInfo:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    filesystem: str
    is_removable: bool


class StorageManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._retention_task: asyncio.Task | None = None
        self._running = False

    # ---- path resolution -------------------------------------------------

    def _target_root(self, target: StorageTarget | None) -> Path:
        if target is None:
            return Path(self.cfg.get("storage.targets.local.path", "recordings"))
        return Path(target.path)

    def recording_dir_for_camera(self, camera: Camera) -> Path:
        with session_scope() as db:
            target = db.get(StorageTarget, camera.storage_target_id) if camera.storage_target_id else None
            root = self._target_root(target)
        safe_name = "".join(c for c in camera.name if c.isalnum() or c in " -_").strip() or f"camera{camera.id}"
        return root / f"cam{camera.id}_{safe_name}".replace(" ", "_")

    def snapshot_dir_for_camera(self, camera: Camera) -> Path:
        return self.recording_dir_for_camera(camera) / "snapshots"

    # ---- mount / disk discovery -------------------------------------------

    def list_available_mounts(self) -> list[MountInfo]:
        """Scans standard removable-media mount roots plus the recording
        root's own filesystem, for the Storage settings "browse" picker."""
        found: dict[str, MountInfo] = {}

        for root in CANDIDATE_MOUNT_ROOTS:
            root_path = Path(root)
            if not root_path.exists():
                continue
            for entry in root_path.iterdir():
                if not entry.is_dir():
                    continue
                info = self._usage_for_path(entry, removable=True)
                if info:
                    found[str(entry)] = info

        local_root = Path(self.cfg.get("storage.targets.local.path", "recordings")).resolve()
        local_root.mkdir(parents=True, exist_ok=True)
        info = self._usage_for_path(local_root, removable=False)
        if info:
            found[str(local_root)] = info

        return list(found.values())

    def _usage_for_path(self, path: Path, removable: bool) -> MountInfo | None:
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            return None
        fs_type = self._filesystem_type(path)
        return MountInfo(
            path=str(path),
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
            filesystem=fs_type,
            is_removable=removable,
        )

    def _filesystem_type(self, path: Path) -> str:
        try:
            with open("/proc/mounts", "r") as fh:
                mounts = [line.split() for line in fh]
        except OSError:
            return "unknown"
        best_match = ("", "unknown")
        resolved = str(path.resolve())
        for parts in mounts:
            if len(parts) < 3:
                continue
            mount_point, fs_type = parts[1], parts[2]
            if resolved.startswith(mount_point) and len(mount_point) > len(best_match[0]):
                best_match = (mount_point, fs_type)
        return best_match[1]

    def target_usage(self, target: StorageTarget) -> MountInfo | None:
        return self._usage_for_path(Path(target.path), removable=(target.kind != "local"))

    # ---- retention / cleanup ------------------------------------------------

    async def start_retention_loop(self) -> None:
        self._running = True
        self._retention_task = asyncio.create_task(self._retention_loop())

    def stop_retention_loop(self) -> None:
        self._running = False
        if self._retention_task:
            self._retention_task.cancel()

    async def _retention_loop(self) -> None:
        interval = self.cfg.get("storage.retention.check_interval_minutes", 15) * 60
        while self._running:
            try:
                await asyncio.get_event_loop().run_in_executor(None, self.run_retention_pass)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Retention pass failed")
            await asyncio.sleep(interval)

    def run_retention_pass(self) -> None:
        """Synchronous; safe to call from a thread executor or a one-off
        admin-triggered "clean up now" API call."""
        import datetime

        max_age_days = self.cfg.get("storage.retention.max_age_days", 30)
        max_usage_pct = self.cfg.get("storage.retention.max_usage_percent", 85)
        min_free_pct = self.cfg.get("storage.retention.min_free_percent", 10)
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max_age_days)

        with session_scope() as db:
            old_recordings = (
                db.query(Recording)
                .filter(Recording.started_at < cutoff)
                .filter(Recording.locked.is_(False))
                .all()
            )
            for rec in old_recordings:
                self._delete_recording_file(rec)
                db.delete(rec)
            if old_recordings:
                logger.info("Retention: removed %d recording(s) older than %d days", len(old_recordings), max_age_days)

        # Usage-percent based cleanup: if any target is over threshold,
        # delete oldest unlocked recordings on that target until under it.
        with session_scope() as db:
            targets = db.query(StorageTarget).all()
            for target in targets:
                info = self.target_usage(target)
                if info is None or info.total_bytes == 0:
                    continue
                used_pct = (info.used_bytes / info.total_bytes) * 100
                free_pct = (info.free_bytes / info.total_bytes) * 100
                if used_pct < max_usage_pct and free_pct > min_free_pct:
                    continue

                logger.warning(
                    "Storage target '%s' at %.1f%% used (free %.1f%%) -- pruning oldest recordings",
                    target.name, used_pct, free_pct,
                )
                camera_ids = [c.id for c in target.cameras]
                candidates = (
                    db.query(Recording)
                    .filter(Recording.camera_id.in_(camera_ids))
                    .filter(Recording.locked.is_(False))
                    .order_by(Recording.started_at.asc())
                    .all()
                )
                for rec in candidates:
                    self._delete_recording_file(rec)
                    db.delete(rec)
                    db.flush()
                    info = self.target_usage(target)
                    if info is None or info.total_bytes == 0:
                        break
                    used_pct = (info.used_bytes / info.total_bytes) * 100
                    free_pct = (info.free_bytes / info.total_bytes) * 100
                    if used_pct < max_usage_pct and free_pct > min_free_pct:
                        break

    def _delete_recording_file(self, rec: Recording) -> None:
        try:
            path = Path(rec.file_path)
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.error("Failed to delete recording file %s: %s", rec.file_path, exc)
