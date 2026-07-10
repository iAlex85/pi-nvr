from __future__ import annotations

import datetime


def test_ffmpeg_command_stream_copy_when_no_overlay(tmp_project):
    from app.config import Config
    from app.recording.engine import RecordingEngine
    from app.models import RecordingMode

    cfg = Config(tmp_project / "config.yaml")
    cfg.set("recording.overlay.timestamp", False)
    cfg.set("recording.overlay.camera_name", False)

    engine = RecordingEngine(cfg, storage_manager=None)
    cmd = engine._build_ffmpeg_command(1, "rtsp://cam/stream", tmp_project / "out", RecordingMode.continuous)

    assert "-c:v" in cmd
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert "libx264" not in cmd


def test_ffmpeg_command_uses_encode_when_overlay_enabled(tmp_project, db_session):
    from app.config import Config
    from app.recording.engine import RecordingEngine
    from app.models import Camera, CameraProtocol, RecordingMode

    camera = Camera(id=1, name="Test Cam", protocol=CameraProtocol.rtsp, rtsp_url="rtsp://x")
    db_session.add(camera)
    db_session.flush()
    db_session.commit()

    cfg = Config(tmp_project / "config.yaml")
    engine = RecordingEngine(cfg, storage_manager=None)
    cmd = engine._build_ffmpeg_command(1, "rtsp://cam/stream", tmp_project / "out", RecordingMode.continuous)

    assert "libx264" in cmd
    assert "-vf" in cmd


def test_ffmpeg_command_motion_mode_uses_ring_buffer(tmp_project):
    from app.config import Config
    from app.recording.engine import RecordingEngine, RING_BUFFER_SEGMENTS
    from app.models import RecordingMode

    cfg = Config(tmp_project / "config.yaml")
    cfg.set("recording.overlay.timestamp", False)
    cfg.set("recording.overlay.camera_name", False)

    engine = RecordingEngine(cfg, storage_manager=None)
    cmd = engine._build_ffmpeg_command(1, "rtsp://cam/stream", tmp_project / "out", RecordingMode.motion)

    assert "-segment_wrap" in cmd
    assert cmd[cmd.index("-segment_wrap") + 1] == str(RING_BUFFER_SEGMENTS)


def test_schedule_within_window(tmp_project):
    from app.recording import scheduler

    scheduler.set_schedule(1, [{"day": "mon", "start": "08:00", "end": "18:00"}])

    inside = datetime.datetime(2026, 7, 6, 12, 0)  # a Monday
    outside = datetime.datetime(2026, 7, 6, 20, 0)  # same Monday, after hours
    wrong_day = datetime.datetime(2026, 7, 7, 12, 0)  # Tuesday

    assert scheduler.is_within_schedule(1, now=inside) is True
    assert scheduler.is_within_schedule(1, now=outside) is False
    assert scheduler.is_within_schedule(1, now=wrong_day) is False


def test_schedule_rejects_invalid_day(tmp_project):
    import pytest
    from app.recording import scheduler

    with pytest.raises(ValueError):
        scheduler.set_schedule(1, [{"day": "someday", "start": "08:00", "end": "18:00"}])


def test_schedule_no_windows_means_never_recording(tmp_project):
    from app.recording import scheduler

    assert scheduler.is_within_schedule(999) is False
