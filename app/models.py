"""ORM models for Pi-NVR. SQLite-backed, SQLAlchemy 2.0 declarative style."""
from __future__ import annotations

import datetime
import enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)


class CameraProtocol(str, enum.Enum):
    rtsp = "rtsp"
    onvif = "onvif"
    mjpeg = "mjpeg"


class RecordingMode(str, enum.Enum):
    off = "off"
    continuous = "continuous"
    motion = "motion"
    scheduled = "scheduled"


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    protocol: Mapped[CameraProtocol] = mapped_column(Enum(CameraProtocol))
    rtsp_url: Mapped[str] = mapped_column(String(512))
    rtsp_substream_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    onvif_host: Mapped[str | None] = mapped_column(String(128), nullable=True)
    onvif_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    onvif_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    onvif_password_enc: Mapped[str | None] = mapped_column(String(256), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_enc: Mapped[str | None] = mapped_column(String(256), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    recording_mode: Mapped[RecordingMode] = mapped_column(
        Enum(RecordingMode), default=RecordingMode.off
    )
    motion_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_target_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage_targets.id"), nullable=True
    )

    resolution_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rotate_degrees: Mapped[int] = mapped_column(Integer, default=0)
    mirror: Mapped[bool] = mapped_column(Boolean, default=False)

    supports_ptz: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    storage_target: Mapped["StorageTarget"] = relationship(back_populates="cameras")
    recordings: Mapped[list["Recording"]] = relationship(
        back_populates="camera", cascade="all, delete-orphan"
    )
    motion_events: Mapped[list["MotionEvent"]] = relationship(
        back_populates="camera", cascade="all, delete-orphan"
    )
    ptz_presets: Mapped[list["PTZPreset"]] = relationship(
        back_populates="camera", cascade="all, delete-orphan"
    )
    motion_zones: Mapped[list["MotionZone"]] = relationship(
        back_populates="camera", cascade="all, delete-orphan"
    )


class StorageTarget(Base):
    __tablename__ = "storage_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    path: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(32), default="local")  # local/usb/network
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    cameras: Mapped[list["Camera"]] = relationship(back_populates="storage_target")


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"))
    file_path: Mapped[str] = mapped_column(String(1024))
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    trigger: Mapped[str] = mapped_column(String(32))  # continuous/motion/manual/scheduled
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)  # exempt from cleanup

    camera: Mapped["Camera"] = relationship(back_populates="recordings")


class MotionEvent(Base):
    __tablename__ = "motion_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"))
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    score: Mapped[float] = mapped_column(Float)  # relative motion magnitude
    bounding_box: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "x,y,w,h"
    snapshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    recording_id: Mapped[int | None] = mapped_column(
        ForeignKey("recordings.id"), nullable=True
    )

    camera: Mapped["Camera"] = relationship(back_populates="motion_events")


class MotionZone(Base):
    """Polygon zone (as JSON string of points, 0-1 normalized coords) that is
    either an 'include' or 'exclude' zone for motion detection."""

    __tablename__ = "motion_zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"))
    zone_type: Mapped[str] = mapped_column(String(16))  # "include" or "exclude"
    points_json: Mapped[str] = mapped_column(Text)

    camera: Mapped["Camera"] = relationship(back_populates="motion_zones")


class PTZPreset(Base):
    __tablename__ = "ptz_presets"
    __table_args__ = (UniqueConstraint("camera_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"))
    name: Mapped[str] = mapped_column(String(64))
    onvif_token: Mapped[str | None] = mapped_column(String(128), nullable=True)

    camera: Mapped["Camera"] = relationship(back_populates="ptz_presets")


class EventLog(Base):
    """Generic system/recording/motion/error log, queryable + downloadable
    from the UI without shelling out to journalctl."""

    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    level: Mapped[str] = mapped_column(String(16))  # INFO/WARNING/ERROR
    category: Mapped[str] = mapped_column(String(32))  # system/recording/motion/auth
    message: Mapped[str] = mapped_column(Text)
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id"), nullable=True)
