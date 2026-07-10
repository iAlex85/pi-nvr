from __future__ import annotations

import datetime


def test_usage_for_path_returns_none_for_missing_path(tmp_project):
    from app.config import Config
    from app.storage.manager import StorageManager

    cfg = Config(tmp_project / "config.yaml")
    mgr = StorageManager(cfg)
    result = mgr._usage_for_path(tmp_project / "does-not-exist-at-all", removable=False)
    assert result is None


def test_usage_for_path_returns_stats_for_real_path(tmp_project):
    from app.config import Config
    from app.storage.manager import StorageManager

    cfg = Config(tmp_project / "config.yaml")
    mgr = StorageManager(cfg)
    result = mgr._usage_for_path(tmp_project, removable=False)
    assert result is not None
    assert result.total_bytes > 0
    assert result.free_bytes >= 0


def test_retention_pass_deletes_old_unlocked_recordings(tmp_project, db_session):
    from app.config import Config
    from app.storage.manager import StorageManager
    from app.models import Camera, CameraProtocol, Recording

    cfg = Config(tmp_project / "config.yaml")
    cfg.set("storage.retention.max_age_days", 30)

    camera = Camera(name="Cam", protocol=CameraProtocol.rtsp, rtsp_url="rtsp://x")
    db_session.add(camera)
    db_session.flush()

    old_file = tmp_project / "old.mp4"
    old_file.write_bytes(b"fake video data")
    new_file = tmp_project / "new.mp4"
    new_file.write_bytes(b"fake video data")

    old_rec = Recording(
        camera_id=camera.id,
        file_path=str(old_file),
        started_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=60),
        trigger="continuous",
    )
    new_rec = Recording(
        camera_id=camera.id,
        file_path=str(new_file),
        started_at=datetime.datetime.now(datetime.timezone.utc),
        trigger="continuous",
    )
    db_session.add_all([old_rec, new_rec])
    db_session.commit()

    mgr = StorageManager(cfg)
    mgr.run_retention_pass()

    assert not old_file.exists()
    assert new_file.exists()

    from app.database import session_scope
    with session_scope() as db:
        remaining = db.query(Recording).all()
        assert len(remaining) == 1
        assert remaining[0].file_path == str(new_file)


def test_retention_pass_never_deletes_locked_recordings(tmp_project, db_session):
    from app.config import Config
    from app.storage.manager import StorageManager
    from app.models import Camera, CameraProtocol, Recording

    cfg = Config(tmp_project / "config.yaml")
    cfg.set("storage.retention.max_age_days", 1)

    camera = Camera(name="Cam", protocol=CameraProtocol.rtsp, rtsp_url="rtsp://x")
    db_session.add(camera)
    db_session.flush()

    locked_file = tmp_project / "locked.mp4"
    locked_file.write_bytes(b"important evidence")

    rec = Recording(
        camera_id=camera.id,
        file_path=str(locked_file),
        started_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=100),
        trigger="motion",
        locked=True,
    )
    db_session.add(rec)
    db_session.commit()

    mgr = StorageManager(cfg)
    mgr.run_retention_pass()

    assert locked_file.exists()
